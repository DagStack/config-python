# ADR-0001 (config-python): VaultSource — SDK choice and renewal strategy

- **Status:** accepted
- **Revision:** 1.0 (2026-05-03)
- **Date:** 2026-05-03
- **Architect review:** ai-systems-architect (proposed round 2026-05-03)
- **Related:**
  [config-spec ADR-0002 §6](https://github.com/dagstack/config-spec/blob/main/adr/0002-secret-references-and-sources.md#6-pilot-adapter--vaultsource-hashicorp-vault-kv-v2),
  [hvac upstream](https://github.com/hvac/hvac).

## Context

ADR-0002 in `dagstack/config-spec` mandates a HashiCorp Vault adapter for
the Phase 2 SecretSource roll-out across the three bindings
(`config-python`, `config-typescript`, `config-go`). The cross-binding
spec leaves three implementation choices to each binding:

1. Which Vault SDK to use.
2. How to expose the SDK as an opt-in dependency (extras / peer-deps /
   sub-modules) so consumers using only file sources do not pay the
   dependency cost.
3. How to handle Vault token renewal (lease lifecycle, periodic
   `auth/token/renew-self`, error paths).

This ADR records the choices made for the Python binding. It does
not amend the spec — every choice here stays inside the
`SecretSource` contract from ADR-0002 §2.

## Decision

### 1. SDK — `hvac>=2.0,<3.0`

`hvac` is the canonical Python client for HashiCorp Vault. Considered
alternatives:

- `python-vault`, `pyvault` — both abandoned (`< 5` GitHub stars,
  no commits since 2019).
- Manual `requests`/`httpx` against the HTTP API — feasible for the
  narrow KV v2 path we exercise, but loses the ergonomics of the
  authentication helpers (`auth.approle.login`,
  `auth.kubernetes.login`) and any future maintenance burden falls
  on the binding rather than upstream.

`hvac` covers every Phase 2 capability we need (KV v2 reads with
`?version=`, AppRole login, Kubernetes ServiceAccount login, namespace
support) and stays maintained by SeatGeek's open-source team. Pin
`>=2.0,<3.0` — major-2 brought breaking renames; pinning the next
major prevents silent upgrades when 3.x lands.

### 2. Packaging — `[vault]` extra

`pyproject.toml`:

```toml
[project.optional-dependencies]
vault = ["hvac>=2.0,<3.0"]
```

Consumers using only `YamlFileSource` install with
`pip install dagstack-config` — no `hvac`, no `requests`, no transitive
TLS dependencies. Consumers wanting the Vault adapter install with
`pip install dagstack-config[vault]`.

The Vault module imports `hvac` at module-load time and raises
`ImportError` with an actionable hint when the extra is missing:

```python
try:
    import hvac
    from hvac.exceptions import ...
except ImportError as exc:
    msg = (
        "VaultSource requires the `[vault]` extra. Install with: "
        "pip install dagstack-config[vault]"
    )
    raise ImportError(msg) from exc
```

This delivers a fast, readable diagnosis rather than the cryptic
`ModuleNotFoundError: No module named 'hvac'` an operator would see at
the first reference to `VaultSource`.

### 3. Sync vs async — sync-first

`hvac` is sync-only as of v2.4. The Python `dagstack.config.secrets`
contract declares two parallel protocols (`SecretSource` for sync,
`AsyncSecretSource` for async) per ADR-0002 §2; Phase 2 ships only the
sync `SecretSource` interface for `VaultSource`.

For asyncio consumers (FastAPI, Starlette, etc.), wrap calls in
`asyncio.to_thread`:

```python
api_key = await asyncio.to_thread(cfg.get_string, "llm.api_key")
```

`asyncio.to_thread` is acceptable for cold-start eager mode (each
secret resolves once at startup) and tolerable for the lazy path under
modest concurrency. If the upstream Vault community ships an async
client (or `hvac` adopts one), Phase 3 can land an
`AsyncSecretSource`-conformant variant without changing the
`SecretSource` contract. Tracked as a future improvement; not blocking.

### 4. Token renewal — Phase 2 boundary

Vault tokens carry a TTL. `VaultSource` does **not** spawn a renewal
background task in Phase 2 — token renewal is deferred to a follow-up
PR alongside the AsyncSecretSource implementation, where an event-loop
hook is the natural home for the renewal timer.

For Phase 2, operators have three workable patterns:

1. **Long TTL plus restart.** Issue Vault tokens with a TTL longer
   than the application's expected uptime (or a renewal cadence
   handled outside the application — e.g., a Kubernetes init-container
   renewing at SIGTERM). Process restart picks up a fresh token.
2. **AppRole.** AppRole `secret_id` is a credential, not a session;
   `VaultSource` performs `auth/approle/login` at construction time
   and the resulting token has a TTL that the operator controls
   through Vault's role configuration. Restart re-logs-in.
3. **Kubernetes ServiceAccount.** The projected SA JWT is renewed by
   the kubelet on a 60-minute cadence; re-login is cheap.

The Phase 3 token-renewal hook will internally call
`client.auth.token.renew_self()` at half-TTL with exponential
back-off on `Forbidden`. The cache invariants and observability hooks
land together with `Config.refresh_secrets()` so consumers see one
coherent rotation story.

### 5. Test strategy

Phase 2 ships:

- **Unit tests** with `unittest.mock.patch("dagstack.config.vault.hvac.Client")`
  for path parsing, KV v2 envelope handling, ``#field`` projection,
  ``?version=`` query, and auth method dispatch. ~24 tests, ~85%
  coverage of `vault.py`.

- **End-to-end test** that wires `VaultSource` through `Config.load_from`
  with a mocked client to verify the loader picks the `vault` scheme.

Deferred to a follow-up PR alongside the conformance fixtures from
config-spec issue #18 slice 2:

- **Integration tests** with `testcontainers` against `vault:1.15` in
  dev mode, with a seed script populating known KV v2 paths.
- **Conformance suite** runs against `phase2_secrets_vault`-tagged
  fixtures, gated on `DAGSTACK_CONFORMANCE_VAULT_ADDR`.

This split keeps the Phase 2 PR fast (no Docker dependency in the unit
suite) and lets us land the cross-binding fixture set in lockstep
with TypeScript and Go bindings.

## Consequences

### Positive

- **Zero dependency cost** for consumers using only file sources;
  `hvac` arrives only when the operator opts in.
- **First-class auth coverage** — Token, AppRole, and Kubernetes
  ServiceAccount in Phase 2; future auth methods (AWS IAM, JWT/OIDC)
  add as one extra `dataclass` + login dispatch each.
- **Maintained upstream** — `hvac` is the de facto Python Vault
  client; bug reports flow through one well-known channel.
- **Diagnostic ergonomics** — the `[vault]` extra import-time error
  tells the operator exactly what to install.

### Negative

- **No async path in Phase 2.** Asyncio consumers wrap with
  `asyncio.to_thread`; tolerable for cold-start eager mode and modest
  concurrency, suboptimal under high concurrent first-touch load.
  Mitigated by the lazy-resolution caching: each unique reference
  resolves once.
- **No automatic renewal in Phase 2.** Operators choose between long
  TTLs and external renewal (init-container, sidecar). Mitigated by
  the AppRole and Kubernetes auth flows, which both produce tokens
  with operator-controlled TTLs.
- **`hvac` API stability risk.** The `hvac>=2.0,<3.0` pin keeps minor
  breaks localised; major-3 will require a binding update with a
  release note.

### Neutral

- **Pydantic / zod / proto schemas are unaffected.** A
  `cfg.get_section("db", DbConfig)` over a Vault-backed `password`
  field works exactly the same as over an env-backed one — the
  resolution happens before schema validation.

## Implementation links

- `src/dagstack/config/vault.py` — module entry point.
- `tests/test_vault_source.py` — unit suite.
- [config-spec ADR-0002 §6](https://github.com/dagstack/config-spec/blob/main/adr/0002-secret-references-and-sources.md#6-pilot-adapter--vaultsource-hashicorp-vault-kv-v2)
  — normative spec for adapter behaviour.
- Follow-up issues:
  - testcontainers-based integration suite (lands with config-spec
    issue #18 slice 2).
  - AsyncSecretSource implementation + token renewal background hook.

## Out of scope

- KV v1 (Phase 3 if any operator requests it).
- Dynamic secrets with leases (`database/creds/...`) — requires
  background-renewal infrastructure that lands with the
  AsyncSecretSource follow-up.
- Vault Agent integration, Consul Template, Banzai Cloud Bank-Vaults
  — out of scope for the binding; these are deployment-time concerns
  the operator runs alongside the application.
- Token revocation on `VaultSource.close()` — operators that want it
  call `client.auth.token.revoke_self()` themselves before close.
