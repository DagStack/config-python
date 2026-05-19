# ADR-0002: Secret references and SecretSource adapters

- **Status:** accepted
- **Revision:** 1.1 (2026-05-03)
- **Date:** 2026-05-03
- **Architect review:** ai-systems-architect (proposed round 2026-05-03)
- **Supersedes:** —
- **Amends:** ADR-0001 §6 (Secrets handling — Phase 2 placeholder), §8 (Source adapters)
- **Revision history:**
  - v1.0 (2026-05-03) — initial design. Introduces `${secret:<scheme>:<path>}` reference syntax,
    a separate `SecretSource` contract (vs `ConfigSource`), a `SecretRef` opaque value type for
    lazy resolution, and a normative pilot adapter `VaultSource` (KV v2). Resolves the open
    question from ADR-0001 v2.1 backlog item 2 ("ConfigSource vs SecretSource separation") in
    favour of two parallel interfaces with a shared loader.
  - v1.1 (2026-05-03) — same-day grammar tightening per architect re-review and linguist gap
    analysis. Formalised `??` escape for literal `?` inside path, `RFC 3986` percent-encoding
    for `query_value`, explicit Escape rules paragraph after the EBNF. No semantic changes;
    no breaking change for any binding implementation that has not yet shipped.
- **Related:** ADR-0001 (base spec), `_meta/secret_patterns.yaml` (masking source of truth),
  HashiCorp Vault KV v2, AWS Secrets Manager, Kubernetes ExternalSecrets operator,
  Spring Cloud Config (`{cipher}` decryptor), Mozilla SOPS, kustomize `secretGenerator`.

## Context

ADR-0001 fixed Phase 1 secrets handling as `${ENV_VAR}` interpolation plus value masking driven
by `_meta/secret_patterns.yaml`. This is sufficient for single-process deployments where the
operator is willing to pre-stage every credential into the process environment (typically through
`docker compose --env-file`, a Kubernetes `Secret` mounted as env, or a `.env` file picked up by
`python-dotenv` or `dotenv`).

Operator feedback from the first pilot consumer identified three real-world pain points the
env-only model cannot cover:

1. **Centralised secret lifecycle.** A staging deploy with three services consuming the same
   `OPENAI_API_KEY` requires three separate env-var injection points; rotating the key means
   rolling all three. A central secret manager (HashiCorp Vault, AWS Secrets Manager, GCP Secret
   Manager, Azure Key Vault, K8s `Secret`) is the standard answer, but ADR-0001 provides no
   contract for fetching values from one.

2. **Audit and access control.** `OPENAI_API_KEY` in a process environment is visible to any
   sibling process under the same UID, leaks into core dumps and `/proc/<pid>/environ` (or `ps eww`) output, and is read at
   container start time without an audit trail. A secret manager fetched on demand with a
   short-lived token records who fetched what and when.

3. **Per-environment scoping without per-environment YAML.** Today the operator either commits
   `app-config.staging.yaml` with `api_key: ${OPENAI_API_KEY_STAGING}` and rebinds env vars, or
   maintains parallel secret stores per environment with the same key names. A namespaced secret
   reference (`${secret:vault:secret/data/dagstack/staging/openai}`) lets one YAML serve all
   environments and lets the secret manager handle per-environment partitioning.

Architect note from ADR-0001 v2.1 backlog item 2 ("ConfigSource vs SecretSource separation") flagged the design risk:

> **`ConfigSource` vs `SecretSource` separation** — secrets backends have substantially
> different semantics (lazy resolution, rotation, leases); mixing them in the §8.3 example may
> produce a leaky abstraction. Consider two sub-contracts with a marker capability
> (`kind: "tree" | "secret_bag" | "lazy_secret"`) or two parallel interfaces.

This ADR resolves that open question by adopting **two parallel interfaces** (`ConfigSource`
returns trees, `SecretSource` resolves single keys). The two are first-class peers under a
single `Config.loadFrom([...])` call.

### Prior art

- **HashiCorp Vault** — the canonical secret manager. KV v1 / KV v2 storage engines; auth
  methods (token, AppRole, Kubernetes ServiceAccount token, AWS IAM, JWT/OIDC); leases for dynamic secrets;
  namespace tenancy. The reference target adapter for this ADR.
- **AWS Secrets Manager** — JSON-typed secrets with versioning (`AWSCURRENT` / `AWSPENDING` /
  `AWSPREVIOUS`); rotation lambdas. ARN-style addressing.
- **GCP Secret Manager** — versioned secrets, per-key IAM, replication policies. Resource-name
  addressing (`projects/<p>/secrets/<n>/versions/<v>`).
- **Kubernetes `Secret`** — `data: { key: base64(value) }`, mounted as files or env. Companion
  operators: External Secrets Operator (ESO), Sealed Secrets, Reloader.
- **Mozilla SOPS / age** — encrypted-at-rest config files with a KMS-managed key. File-source
  semantics, not lazy-resolution semantics — fits `ConfigSource`, not `SecretSource`.
- **Spring Cloud Config** — `{cipher}…` token in YAML decrypted by the config server. Single
  syntax, server-side resolution. Closest to the syntax we adopt below; differs in that it has
  one decryption backend and we want pluggability.
- **etcd-confd / consul-template** — template-engine model. Treats secrets and config as
  templated text; rendered at process-start. We deliberately avoid the template-engine path
  because it disables typed access (every value becomes a rendered string).
- **dotenv-vault, Doppler, Infisical** — SaaS aggregators with a uniform CLI. Out of scope to
  endorse a specific SaaS, but the adapter contract we define is identical in shape to their
  REST APIs.

The community converged on two distinct concerns that this ADR keeps separated:

- **Reference syntax inside YAML** — `${cipher}`, `${secret://...}`, `!vault`, `<<: !include`.
- **Backend pluggability** — vendor-specific clients delivering a value for a key.

## Decision

### 1. Reference syntax — `${secret:<scheme>[:<path>]}`

A new normative interpolation token, parallel to ADR-0001 §2 `${VAR}`:

```
${secret:<scheme>:<path>}                → resolved value of <scheme>+<path>
${secret:<scheme>:<path>:-<default>}     → fallback to literal <default> if reference does not resolve
```

Examples:

```yaml
llm:
  api_key:    ${secret:env:OPENAI_API_KEY}                       # passthrough — default scheme = env
  fallback:   ${secret:env:OPENAI_API_KEY:-sk-dev-placeholder}
database:
  password:   ${secret:vault:secret/data/dagstack/prod/db#password}
external_api:
  token:      ${secret:awssm:arn:aws:secretsmanager:eu-west-1:...:secret/openai-key}
  regional:   ${secret:gcpsm:projects/foo/secrets/openai/versions/latest}
```

**Grammar (normative).**

```
secret_ref     := "${" "secret" ":" scheme ":" path_with_query [field_proj] [":-" default] "}"
scheme         := [a-z][a-z0-9_]*                # lowercase ASCII; matches SecretSource.scheme
path_with_query:= path ["?" query]
path           := <any chars except "}", "#"; literal "?" inside path is "??", literal "#" is "##", ":-" is "::-">
query          := query_kv ("&" query_kv)*       # backend-specific options
query_kv       := query_key "=" query_value
query_key      := [a-z][a-z0-9_]*
query_value    := <percent-encoded per RFC 3986; literal "&", "=", "}", "#" MUST be %-encoded>
field_proj     := "#" field                      # sub-key projection per §1.2
field          := <any chars except "}">
default        := <any chars except "}">
```

The `?key=value` query block is reserved for backend-specific options. The Phase 2 normative
key is `version` (Vault adapter only — `?version=3` selects an explicit KV v2 version). Other
keys are adapter-defined; bindings **MUST** reject unknown keys with `secret_unresolved` rather
than silently dropping them. The order is fixed: `path` → optional `?query` → optional `#field`
→ optional `:-default` → closing `}`.

**Escape rules.** Literal `#`, `?`, and `:-` inside a path segment are escaped by doubling
(`##`, `??`, `::-`) — this lets paths carry these characters without disambiguating them from
the structural separators. Literal `&`, `=`, `}`, `#` inside a query value MUST be
percent-encoded per RFC 3986; this aligns with HTTP query-string conventions and makes
standard-library URL helpers (Python `urllib.parse.quote`, TypeScript `encodeURIComponent`, Go
`url.QueryEscape`) correct decoders.

**Why this shape (and not the alternatives we evaluated):**

| Alternative | Rejected because |
|---|---|
| `${secret:openai/api-key}` (no scheme) | Forces a single global default backend; can't compose Vault + AWS-SM in one config. |
| `secret://vault/openai/api-key` (URI without `${}`) | Breaks ADR-0001 §2 grammar — the token is no longer recognisable as "an interpolation site"; YAML linters and `_meta/coercion.yaml` walker would need a second mode. |
| YAML custom tag `!secret vault/openai/api-key` | Non-portable across YAML 1.2 strict implementations (pyyaml strict mode trips on unknown tags; `yaml.v3` requires `UnmarshalYAML` per type). Also loses the env-default fallback (`:-`). |
| Structured object `{secret: {backend: vault, path: …}}` | Verbose at every call site; wraps a scalar field in an object, breaking `getString` ergonomics. Useful as an *escape hatch* (see §1.3 below) but not as the primary form. |

The chosen shape preserves the full ADR-0001 §2 grammar — `${...}` is still the only interpolation
site — extends the token namespace by one prefix, and stays a string scalar in YAML. It composes
trivially with §3 layering (a `local` layer can override the secret with a literal for tests).

#### 1.1. The `env` scheme — backwards compatibility

`${secret:env:OPENAI_API_KEY}` is **semantically identical** to `${OPENAI_API_KEY}`. This is
explicitly normative so that:

1. Bindings can implement env interpolation as a degenerate case of secret resolution
   (`EnvSecretSource` is automatically registered on every loader).
2. Migration from Phase 1 is a mechanical rename, not a behavioural change. An operator can
   `sed -i 's/${\([A-Z_]*\)}/${secret:env:\1}/g'` their `app-config.yaml` and observe identical
   runtime behaviour, then later swap individual references to `vault:` / `awssm:` without
   editing surrounding fields.
3. The default-value form `${VAR:-fallback}` and `${secret:env:VAR:-fallback}` are equivalent.

Bindings **MUST NOT** treat `env` as a privileged scheme — it is registered through the same
`SecretSource` interface as Vault.

#### 1.2. Path field separator — `#` for sub-key addressing

Most secret managers store a "secret" as a JSON object with multiple fields (Vault KV v2
`{ password: ..., username: ... }`, AWS-SM JSON-typed secret). The path grammar reserves `#` to
address a sub-key inside one stored object:

```
${secret:vault:secret/data/dagstack/prod/db#password}
${secret:vault:secret/data/dagstack/prod/db#username}
```

Both references hit the same Vault read; the loader **MUST** cache by `<scheme>:<path-up-to-#>`
so a single Vault round-trip serves both. If `#` is omitted and the resolved value is an object,
the binding raises `secret_unresolved` with details template ``reference resolved to object;
specify a sub-key with '#field'`` rather than auto-stringifying. Bindings render the template
in their native string idiom — the literal text is normative; the surrounding quote style is not.

Escaping: a literal `#` inside a path segment is `##`. A literal `}` is `\}`.

#### 1.3. Structured form — escape hatch for advanced cases

For cases where the path doesn't fit on one line (multi-line PEM, complex Vault namespaces with
literal `:` characters), bindings **MAY** accept a structured form:

```yaml
api:
  tls_key:
    !secret
      scheme: vault
      path:   secret/data/dagstack/prod/tls
      field:  private_key.pem
      default: null
```

The structured form is **OPTIONAL** in Phase 2 and **MUST NOT** be required for any conformance
test to pass. Adoption depends on operator demand from the pilot; if uptake is zero by the v0.5
binding releases, the structured form is dropped. This keeps the spec narrow.

### 2. The `SecretSource` contract — separate from `ConfigSource`

The architect note in ADR-0001 v2.1 backlog item 2 ("ConfigSource vs SecretSource separation") considered three options:

- (A) Marker capability on `ConfigSource` (`kind: "tree" | "secret_bag" | "lazy_secret"`).
- (B) Two parallel interfaces.
- (C) `ConfigSource` always eager, `SecretSource` always lazy, composed by `Config.load()`.

**Decision: (B) — two parallel interfaces.** Rationale:

- Type-safety: `ConfigSource.load() -> ConfigTree` is total; `SecretSource.resolve(path) ->
  SecretValue` is keyed and partial. Squashing them under one interface loses both signatures.
- Watch semantics: ADR-0001 §7 watch is a tree-level event. Secret rotation is a key-level event.
  Both signals exist but in different shapes (`ChangeEvent.path` is set-membership; rotation is a
  versioned-value change for one key). Two interfaces, two signals.
- Cache lifecycle: `ConfigSource` results are cached for the process lifetime (or until reload);
  `SecretSource` results may be cached with TTL or per-lease. Combining the two cache strategies
  in one adapter would force adapters to special-case themselves.
- Future composition: option (A) bakes the marker into every adapter even when the adapter is
  pure-tree. (B) keeps each interface minimal.

**Contract.**

```
SecretSource {
  scheme:    string                                    # short scheme name (matches ${secret:<scheme>:...})
  id:        string                                    # human-readable identifier; e.g., "vault:https://vault.example.com"

  resolve(path: string, ctx: ResolveContext): SecretValue   # binding picks sync/async idiom
  close?():  void                                            # release resources
  watch?(path: string, callback: (SecretChangeEvent) => void): Subscription   # optional, Phase 3
}

SecretValue {
  value:        string                # the resolved secret material (always string at the wire level)
  version?:     string                # opaque version id from the backend (Vault metadata.version, AWS-SM VersionId)
  expires_at?:  ISO-8601              # if the backend returns a TTL (Vault dynamic creds, AWS-SM rotation hint)
  source_id:    string                # echoed from SecretSource.id for diagnostics
}

ResolveContext {
  cancellation?: <binding-native cancellation handle>     # Go context.Context, Python asyncio.Task / anyio.CancelScope, etc.
  deadline?:     ISO-8601 / native deadline
  attempt:       int                  # 1-based attempt counter (for retry-aware adapters)
}
```

**Sync vs async** — same rule as ADR-0001 §4: per-binding choice. Go: `Resolve(ctx, path) (SecretValue, error)`.
Python: `def resolve(self, path, ctx)` — sync by default; an async-flavoured parallel protocol
`AsyncSecretSource` with `async def resolve_async(self, path, ctx)` is provided for non-blocking
event loops (the two protocols share the conceptual contract but are distinct types so callers
get correct typing).
TypeScript: `resolve(path, ctx): Promise<SecretValue>` — Promise-based.

**`SecretValue.value` is always a string.** Type coercion happens at the `Config.get*` call site,
exactly like for env-interpolated values (ADR-0001 §4.4). The binding **MUST NOT** attempt to
JSON-parse the value into a sub-tree — the `#field` syntax (§1.2) is the only sanctioned way to
project sub-keys, and it is interpreted at the `<scheme>:<path>` parse step before the adapter
is called.

### 3. `SecretRef` — opaque value type and resolution timing

A `${secret:...}` token in a YAML file does **not** trigger a secret-manager round-trip at
`source.load()` time. Instead, the file source emits a `SecretRef` placeholder at the
corresponding tree leaf:

```
SecretRef {
  scheme:        string
  path:          string                # full path including any #field projection
  default?:      string                # the literal after ":-", if any
  origin_source: string                # ConfigSource.id where this token was found (for diagnostics)
}
```

The merged tree may therefore contain `SecretRef` instances mixed with regular scalars.
Resolution happens at one of three points:

| Trigger | Behaviour |
|---|---|
| `config.get(path)` returns a `SecretRef` value | The binding **MUST** resolve transparently and return the resolved string. (Behavioural parity with env interpolation: callers should not need to know the field is secret-backed.) |
| `config.getString(path)` / `config.getInt(path)` etc. | Resolve transparently; apply primitive coercion to the resolved string per ADR-0001 §4.3. |
| `config.getSection(path, schema)` | Resolve every `SecretRef` inside the subsection, then run the schema validator. |
| `config.snapshot()` | Replace every `SecretRef` with `[MASKED]` (per `_meta/secret_patterns.yaml`). The reference itself is never resolved by `snapshot()`. An audit-mode opt-in (`snapshot(include_secrets=true)` in Python; `snapshot({includeSecrets: true})` in TypeScript; `Snapshot(WithIncludeSecrets())` in Go) MAY resolve and mask by suffix-pattern only. |

**Resolution timing — lazy by default, with eager opt-in.**

- **Lazy (default):** `SecretRef` lives in the tree until the first read of that field. Cold
  start is `O(file IO)` — the loader does not call out to Vault. First access on the read path is
  `O(file IO + secret round-trip)`; subsequent accesses are `O(cache lookup)`.
- **Eager (opt-in):** `Config.loadFrom(sources, eager_secrets=True)` walks the merged tree at
  load time and resolves every `SecretRef`. Cold start is `O(file IO + N × secret round-trip)`,
  but every read after that is `O(cache lookup)` and configuration errors caused by unresolvable
  secrets surface at startup, not at first request.

**Pilot consumer recommendation (long-lived servers): use eager mode.** Surfacing
`secret_unresolved` at startup is observably better than a 500 on the first inbound request.
Ephemeral CLI tools may prefer lazy.

**Caching.** A binding **MUST** cache resolved secrets in-process for the lifetime of the
`Config` object, keyed by `<scheme>:<path-without-#field>`. The cache **MUST** honour
`expires_at` from `SecretValue` if present, returning a cache miss after expiry. Configurable
TTL override per `SecretSource` adapter (`VaultSource(...).with_cache_ttl(seconds)`) is allowed
but binding-specific.

**Forced refresh.** `config.refresh_secrets()` (binding idiom: `RefreshSecrets()` in Go;
`config.refresh_secrets()` in Python; `config.refreshSecrets()` in TS) drops the secret cache
and triggers a re-resolution on next access. This is the manual-rotation hook for Phase 2.
Push-based rotation (Vault lease watcher, AWS-SM rotation event subscription) is deferred to
Phase 3 (§Implementation roadmap).

### 4. Loader integration

`Config.loadFrom([...])` accepts a heterogeneous list of `ConfigSource` and `SecretSource`
instances. The loader dispatches by interface:

```
config := Config.loadFrom([
    YamlFileSource("app-config.yaml"),                   # ConfigSource — provides the tree
    YamlFileSource("app-config.local.yaml"),             # ConfigSource
    EnvSecretSource(),                                   # SecretSource — registered for ${secret:env:...}
    VaultSource(addr="https://vault.example.com",        # SecretSource — registered for ${secret:vault:...}
                auth=AppRoleAuth(role_id=role_id, secret_id=secret_id),
                namespace="dagstack/prod"),
])
```

**Loader rules (normative).**

1. **Source ordering.** `ConfigSource` order continues to define merge priority (ADR-0001 §3).
   `SecretSource` order **does not** define priority — each scheme has at most one registered
   source. Registering two `SecretSource` instances with the same `scheme` is a programming
   error at loader-construction time (`ConfigError(reason=validation_failed, details="duplicate
   SecretSource scheme: vault")`). `validation_failed` (not `source_unavailable`) — the issue is
   in the loader bootstrap configuration, not in a backend's reachability.
2. **Implicit env source.** The loader **MUST** register a default `EnvSecretSource` if none is
   passed explicitly. This guarantees `${secret:env:VAR}` works without ceremony.
3. **Unknown scheme at load time.** If a `${secret:<scheme>:...}` token uses a scheme with no
   registered source, the binding raises `ConfigError(reason=secret_unresolved,
   details="no SecretSource registered for scheme '<scheme>'")` at load time when scanning
   sources, not at first read. (This catches misconfiguration up-front.)
4. **Unknown scheme at parse time.** The token grammar is permissive — any lowercase scheme
   parses. Validation that a scheme has a registered source is the loader's job, not the parser's.

### 5. Error model — three new reasons

Three new entries appended to `_meta/error_reasons.yaml`:

| `name` | `value` | `description` |
|---|---|---|
| `SECRET_UNRESOLVED` | `secret_unresolved` | A `${secret:...}` reference could not be resolved (no source registered, key missing in backend, no default). |
| `SECRET_BACKEND_UNAVAILABLE` | `secret_backend_unavailable` | The secret backend is unreachable (network error, auth failure, timeout). |
| `SECRET_PERMISSION_DENIED` | `secret_permission_denied` | The backend rejected the read with an authorisation error (Vault 403, AWS-SM `AccessDeniedException`). |

**Why three reasons, not one.** Operators react differently to each:
- `secret_unresolved` → check the YAML and the backend key spelling.
- `secret_backend_unavailable` → check network / DNS / credentials at process level.
- `secret_permission_denied` → check the Vault policy / AWS IAM, not the spelling.

Squashing them into one would force the operator to read `details` (not a wire contract per
ADR-0001 §4.5) to decide what to fix. Three distinct `reason` codes give programmatic dispatch.

`source_id` on these errors is the `SecretSource.id` (e.g., `vault:https://vault.example.com`),
not a `ConfigSource.id`. The diagnostic message **MUST** also reference the
`SecretRef.origin_source` so the operator knows which YAML file the bad token came from:

```
ConfigError(
  path        = "llm.api_key",
  reason      = secret_unresolved,
  details     = "vault:secret/data/dagstack/prod/openai → 404 Not Found "
                "(referenced from yaml:app-config.yaml)",
  source_id   = "vault:https://vault.example.com",
)
```

### 6. Pilot adapter — `VaultSource` (HashiCorp Vault KV v2)

The first adapter shipped in all three bindings.

#### 6.1. KV version

**KV v2 only in Phase 2.** KV v1 lacks versioning, soft-delete, and the metadata structure
that the rest of this ADR depends on (`SecretValue.version`). KV v1 support, if requested, lands
in a Phase 3 adapter (`VaultKvV1Source`). Vault deployments still on KV v1 are a small fraction
of operator base; not worth the matrix.

#### 6.2. Auth methods

**Phase 2 mandatory:**
- **Token** — operator supplies `VAULT_TOKEN` directly. The simplest case; covers every
  development scenario and any deployment that already injects a token via init-container.
- **AppRole** — `role_id` + `secret_id`. The default for production CI/CD pipelines.

**Phase 2 optional (binding picks one or both):**
- **Kubernetes ServiceAccount** — `vault.k8s.role` + JWT from
  `/var/run/secrets/kubernetes.io/serviceaccount/token`. Ergonomic for Kubernetes deployments;
  binding-side overhead is small (one Vault `auth/kubernetes/login` call).

**Phase 3 (deferred):**
- AWS IAM, JWT/OIDC, TLS client certificate, GCP, Azure — long tail. Add on demand.

The binding's adapter exposes auth as a typed parameter:

```python
# Python
VaultSource(addr="https://vault.example.com",
            auth=TokenAuth(token=os.environ["VAULT_TOKEN"]),
            namespace="dagstack/prod")
VaultSource(addr=..., auth=AppRoleAuth(role_id=..., secret_id=...))
```

```go
// Go
config.NewVaultSource(addr,
    config.WithVaultToken(os.Getenv("VAULT_TOKEN")),
    config.WithVaultNamespace("dagstack/prod"))
config.NewVaultSource(addr, config.WithVaultAppRole(roleID, secretID))
```

```typescript
// TypeScript
new VaultSource({
  addr: "https://vault.example.com",
  auth: { kind: "token", token: process.env.VAULT_TOKEN },
  namespace: "dagstack/prod",
});
```

#### 6.3. Namespace and path conventions

- **Vault Enterprise namespaces** are passed as the `namespace` parameter at construction time;
  the adapter prepends them automatically. Path in YAML stays namespace-free for portability.
- **KV v2 path layout** — Vault's HTTP API requires `secret/data/<path>` for reads and
  `secret/metadata/<path>` for metadata. The adapter accepts the **logical path**
  (`secret/dagstack/prod/openai`) and rewrites internally to `secret/data/...`. The user-visible
  path matches `vault kv get` CLI ergonomics.

#### 6.4. Versioning

Reading a specific version: `${secret:vault:secret/dagstack/prod/db?version=3#password}`.
Query-string syntax — only `version` is recognised in Phase 2; future query parameters are
backend-specific. If the requested version is destroyed or deleted, the adapter raises
`secret_unresolved`.

Default (no `?version=`) reads the latest version. Cache key includes the requested version so
`?version=3` and `?version=4` are cached independently.

#### 6.5. SDK choice per binding

| Binding | Library | Rationale |
|---|---|---|
| Python | `hvac` (≥2.0) | Maintained, KV v2 first-class, supports namespace, AppRole, Kubernetes ServiceAccount login. |
| TypeScript | `node-vault` (≥0.10) | Most-downloaded, KV v2 supported. Alternative: `@hashicorp/vault-client-typescript` (official, newer, fewer downloads — track for switch when stable). |
| Go | `github.com/hashicorp/vault/api` (official client) | Official; standard in Go ecosystem. |

Each binding records its choice in its per-language ADR with version constraints and a
deprecation policy. SDK is a *transitive* dependency of the `dagstack-config` package — not a
required one. The binding **MUST** make `VaultSource` an opt-in extra:

- Python: `pip install dagstack-config[vault]` → pulls `hvac`.
- TypeScript: peer dependency on `node-vault`; `npm install node-vault`.
- Go: separate sub-module `go.dagstack.dev/config/vault` to avoid pulling Vault SDK into every
  binary that uses just file sources.

This separation ensures dagstack-config without secret backends remains a small, dependency-light
module.

### 7. Migration story for existing consumers

Take a Phase 1 deployment with:

```yaml
llm:
  api_key: ${OPENAI_API_KEY}
```

Phase 2 migration paths, in order of operator effort:

**Step 0 — no change required.** ADR-0001 syntax keeps working. `${OPENAI_API_KEY}` is identical
to `${secret:env:OPENAI_API_KEY}` (§1.1). Operators with no Vault deployment do nothing.

**Step 1 — opt into the secret namespace, still using env.**
```yaml
llm:
  api_key: ${secret:env:OPENAI_API_KEY}
```
Mechanical sed; no behavioural change; readies the field for backend swap.

**Step 2 — point at Vault.**
```yaml
llm:
  api_key: ${secret:vault:secret/dagstack/prod/openai#api_key}
```
Operator stages the secret in Vault, configures `VaultSource` in the loader bootstrap (one
Python / TS / Go line in the application), removes `OPENAI_API_KEY` from the process
environment.

**No breaking change at the YAML wire format.** The Phase 1 `${VAR}` syntax remains valid
indefinitely. Future bindings may emit a `DeprecationWarning` if the operator opts in to a
strict-mode lint, but the syntax is normative and supported.

### 8. Spec-distributed artefacts (additions)

Three new files in `_meta/`:

- **`_meta/secret_schemes.yaml`** — registry of normative secret schemes:

  ```yaml
  version: "1.0"
  schemes:
    - scheme: env
      adapter: EnvSecretSource
      mandatory: true                # always registered
      phase: 2
      kind: in-process
    - scheme: vault
      adapter: VaultSource
      mandatory: false
      phase: 2
      kind: remote
      docs: https://docs.dagstack.dev/config/secrets/vault
    - scheme: awssm
      adapter: AwsSecretsManagerSource
      mandatory: false
      phase: 3
      kind: remote
    - scheme: gcpsm
      adapter: GcpSecretManagerSource
      mandatory: false
      phase: 3
      kind: remote
    - scheme: k8ssecret
      adapter: K8sSecretSource
      mandatory: false
      phase: 3
      kind: in-cluster
  ```

  The file is the source of truth for adapter dispatch in `Config.loadFrom([...])` and for
  human-friendly error messages on unknown schemes (`"available schemes: env, vault; did you
  mean 'vault'?"`).

- **`_meta/secret_ref_grammar.yaml`** — formal grammar for `${secret:...}` (regex fragments per
  binding), escape rules, the `#field` projection, the `?version=` query parameter table.

- **`_meta/error_reasons.yaml`** — append the three new reasons (§5).

- **`_meta/conformance_tags.yaml`** — append two normative tags: `phase2_secrets` (env-scheme
  fixtures, run unconditionally), `phase2_secrets_vault` (Vault-backed fixtures, gated on
  `DAGSTACK_CONFORMANCE_VAULT_ADDR`).

Existing artefacts that gain rows:

- **`_meta/types.yaml`** — add `SecretSource`, `SecretRef`, `SecretValue`, `SecretChangeEvent`,
  `EnvSecretSource`, `VaultSource`. Each row carries Python / TS / Go renderings under the
  stutter-avoidance rule (`SecretSource` → `secret.Source` if a Go binding extracts secrets to a
  sub-package; otherwise `config.SecretSource`).

### 9. Conformance fixtures (additions)

New fixture directories under `conformance/`:

- **`conformance/secret_refs/`** — pure-syntax fixtures. Inputs in YAML with `${secret:env:...}`
  and `${secret:env:...:-default}` references; expected canonical-JSON output after resolution
  with a fixed env vector. Covers `env` scheme only (no live Vault required).
- **`conformance/secret_lazy/`** — lazy-vs-eager behaviour. A `SecretRef` survives `load()`,
  resolves on `get()`, masks under `snapshot()`. Verified through a programmable test runner that
  inspects intermediate state (`config.snapshot()` between `load()` and `get()`).
- **`conformance/secret_errors/`** — error fixtures. `secret_unresolved` (no scheme registered),
  `secret_unresolved` (env var missing without default), `secret_unresolved` (object value
  without `#field`), grammar errors.
- **`conformance/vault/`** — **conditional fixture**. Runs only when an env var
  `DAGSTACK_CONFORMANCE_VAULT_ADDR` is set; spec ships a `docker-compose.yml` for `vault server
  -dev` plus a seeded payload script. CI in each binding gates this on a separate job.

All non-Vault fixtures join the existing `manifest.yaml` with tag `phase2_secrets`. The Vault
fixture lives in a separate manifest section (`phase2_secrets_vault`) flagged
`requires_external_service: true`.

### 10. Cross-binding round-trip CI extension

The cross-binding round-trip CI introduced in 2026-04-23 already verifies canonical-JSON parity
across Python / TS / Go for `ConfigSource`-only inputs. Extension:

1. Add a fixture set under `conformance/cross_binding/secret_refs/` — inputs with
   `${secret:env:...}` references plus a fixed env file.
2. Each binding's runner produces canonical-JSON of the resolved tree.
3. CI compares all three outputs byte-for-byte (`git diff --exit-code` style).
4. Vault adapter parity is verified separately because pulling vault-dev into the cross-binding
   job is heavy. Each binding runs its own Vault integration suite; cross-binding parity is on
   the resolved `SecretValue.value` only (no network in cross-binding CI).

### 11. Subscription extension (preview, Phase 3)

ADR-0001 §7.2 subscription model is extended in Phase 3 with a `SecretChangeEvent`:

```
SecretChangeEvent {
  scheme:     string
  path:       string
  new_value:  string                  # the rotated value
  old_version?: string
  new_version?: string
  source_id:  string
  timestamp:  ISO-8601
}

config.onSecretChange(scheme: string, path: string, callback: (SecretChangeEvent) => void): Subscription
```

Implementation requires push capability from the backend (Vault lease renewal callbacks, AWS-SM
EventBridge, GCP Pub/Sub on rotation). Phase 2 ships only the polling-based `refresh_secrets()`
hook (§3 Forced refresh). The full subscription contract is fixed in a separate ADR-0003 once
Phase 2 is operationally validated.

## Consequences

### Positive

- **Operator-grade secrets** — Vault and (later) cloud secret managers as first-class config
  sources, no more env-var-only model.
- **No breaking change** — `${VAR}` keeps working; `${secret:env:VAR}` is identical. Migration
  is mechanical.
- **Type safety preserved** — `getInt` / `getString` / `getSection` continue to work
  transparently; secret resolution is invisible to the consumer.
- **Pluggability** — the `SecretSource` interface is what every backend implements; new schemes
  ship without changing the spec or the loader.
- **Audit-ready** — every resolution carries `source_id` and the original YAML location;
  `secret_permission_denied` is a distinct error code, separate from "key missing".
- **Cache transparency** — `refresh_secrets()` is a clear, manual rotation hook; per-key
  expiration honoured.

### Negative

- **Operational complexity** — Vault adds a process dependency. Mitigated by the `env` scheme
  remaining the default and by SDK as opt-in extras.
- **Spec surface area increases** — three new `_meta/*.yaml` files, three new error reasons, one
  new interface, one new value type. This is the correct cost of solving the actual problem;
  alternatives (one-size-fits-all `ConfigSource`) were considered and rejected.
- **Resolution-timing surprise** — lazy mode means a secret-related error surfaces at first
  request, not at startup. Mitigated by recommending `eager_secrets=True` for long-lived servers.
- **Per-binding SDK lock-in** — each binding picks one Vault SDK and inherits its bugs.
  Mitigated by sub-packaging (extras, peer-deps, sub-module) — the core binding stays SDK-free.

### Neutral

- **Phase 1 Pydantic `SecretStr` / zod string secret types** continue to work. They wrap
  resolved string values; the `SecretRef` placeholder is invisible to schema validators because
  resolution happens before `getSection` runs the validator.

## Implementation roadmap

### Phase 2 (this ADR — pilot)

1. **Spec changes (`dagstack/config-spec`):**
   - Append three reasons to `_meta/error_reasons.yaml`.
   - Add `_meta/secret_schemes.yaml`, `_meta/secret_ref_grammar.yaml`.
   - Add `SecretSource` / `SecretRef` / `SecretValue` rows to `_meta/types.yaml`.
   - Update §6 of ADR-0001 to point at this ADR (single sentence: "see ADR-0002 for the
     normative spec").
   - Add fixtures: `conformance/secret_refs/`, `conformance/secret_lazy/`,
     `conformance/secret_errors/`, conditional `conformance/vault/`.
   - Extend cross-binding round-trip CI for the `env`-scheme fixtures.
   - Add ADR-0002 itself (this document).

2. **Bindings (Python / TypeScript / Go) — same shape per binding:**
   - New `SecretSource` interface / protocol.
   - `SecretRef` placeholder type (frozen dataclass / readonly object / struct).
   - Loader changes: `loadFrom()` accepts mixed `ConfigSource` + `SecretSource`; `EnvSecretSource`
     auto-registered; `SecretRef` instances emitted by the file-source interpolator.
   - Lazy resolution at `get*` / `getSection` boundaries; eager opt-in flag.
   - `refresh_secrets()` API.
   - `VaultSource` adapter (KV v2, Token + AppRole auth) as an opt-in extra
     (`pip install dagstack-config[vault]` / npm peer-dep / Go sub-module
     `go.dagstack.dev/config/vault`).
   - Snapshot masking includes `SecretRef` placeholders.
   - Conformance runner extension to handle the new fixtures.

3. **Pilot consumer:**
   - Update the consumer's `app-config.yaml` to use `${secret:vault:...}` for secrets, gated by
     a feature flag (e.g. `VAULT_ENABLED=true`); otherwise stays on `${secret:env:...}`.
   - Bootstrap `VaultSource` in the application startup when the flag is on.
   - Smoke-test eager mode at startup.

### Phase 3 (next ADR — ADR-0003 candidate)

- `AwsSecretsManagerSource`, `GcpSecretManagerSource`, `K8sSecretSource`.
- Push-based rotation: `onSecretChange` subscription, Vault lease renewal, AWS EventBridge
  integration.
- KV v1 adapter (`VaultKvV1Source`) if any operator requests it.
- Composed multi-backend resolution (e.g., Vault for prod, env for dev, on the same `vault:`
  scheme) — likely lands as a `CompositeSecretSource` wrapper.

### Out of scope (Phase 2)

- Push-based rotation events (Phase 3).
- KV v1 (Phase 3+).
- Cloud secret managers (Phase 3 — adapter pattern is identical to Vault, just SDK swap).
- Dynamic secrets with leases (Vault `database/creds/...`) — the lease lifecycle requires
  background renewal goroutines / asyncio tasks that are out of scope until subscription
  semantics land in Phase 3.
- Encrypted-at-rest config files (SOPS, age) — these are `ConfigSource`, not `SecretSource`;
  separate adapter, not blocked by this ADR.
- Secret-write API — config is a *read* contract; rotation tooling is a separate concern that
  lives outside dagstack.
- The `!secret` structured form (§1.3) becomes mandatory only if the pilot demands it.

## Open questions

1. **Multiple Vault clusters in one config.** A consumer might want
   `${secret:vault-prod:...}` and `${secret:vault-dr:...}` from two clusters. The §4 loader rule
   says "one source per scheme." Resolution: the operator registers `VaultSource(scheme="vault")`
   and `VaultSource(scheme="vault-dr")` — schemes are arbitrary lowercase identifiers, only
   `env` is reserved. Documented in `_meta/secret_schemes.yaml` as "operator-extensible scheme
   space."

2. **What if a `SecretRef` points at a tree-typed value?** A Vault secret can be
   `{ "username": "...", "password": "...", "tls": { "cert": "...", "key": "..." } }`. The §1.2
   `#field` projection only addresses one level. Phase 2 decision: only one level
   (`#tls.cert` is a single field name with a literal dot, not a path). Multi-level addressing
   waits for operator demand.

3. **Coalescing of in-flight Vault requests.** Two concurrent reads of the same path produce two
   round-trips in the naive implementation. Phase 2 normative: in-process resolution **MAY** be
   coalesced (a per-binding implementation choice); not required, but recommended for the Vault
   adapter. Conformance does not test this.

4. **`ResolveContext.attempt`** — is it observable to adapters or just diagnostic? Phase 2:
   diagnostic only. The loader does not implement automatic retries — that's the adapter's
   business. Adapters MAY use the field to pick a longer timeout on retry; bindings MUST
   increment it monotonically per call chain.

5. **Token rotation for Vault auth itself.** Vault tokens expire. The adapter must renew its
   own token (Vault `auth/token/renew-self`). Phase 2: each binding implements token renewal
   internally, no spec normative; observability events optional. Spec note: this is an adapter
   concern, not a `SecretSource` interface concern.
