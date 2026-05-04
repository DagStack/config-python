# Changelog

All notable changes to `dagstack-config` documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html) pre-1.0 — `0.N.M` + `.devN`/`.rcN`.

## [0.5.0] — 2026-05-04

Phase 2 secrets — `${secret:<scheme>:<path>}` reference syntax with
pluggable `SecretSource` adapters. Pilot adapter for HashiCorp Vault
KV v2 ships in the `[vault]` extra. Spec: ADR-0002.

### Added

- `SecretSource` Protocol (sync) and `AsyncSecretSource` (async-flavoured
  parallel Protocol) — adapter contract for secret backends.
- `SecretRef`, `SecretValue`, `ResolveContext` — value types for
  references and resolution.
- `EnvSecretSource` — mandatory in-process adapter for the `env`
  scheme. `${secret:env:VAR}` is semantically identical to `${VAR}`
  from ADR-0001 §2 (backwards-compat).
- `dagstack.config.vault.VaultSource` — pilot Vault adapter under the
  `[vault]` extra. KV v2 only; `TokenAuth`, `AppRoleAuth`,
  `KubernetesAuth`; namespace support; `?version=N` query; `#field`
  projection. SDK: `hvac>=2.0,<3.0`.
- Three new `ConfigErrorReason` values — `secret_unresolved`,
  `secret_backend_unavailable`, `secret_permission_denied` — for
  operator-actionable dispatch.
- `Config.load_from()` accepts a heterogeneous list of `ConfigSource`
  and `SecretSource` instances. The loader auto-registers
  `EnvSecretSource` if no `SecretSource` is passed; eager scan at
  load time fails fast on unknown schemes per ADR-0002 §4 rule 3.
- Lazy resolution by default (Python: lazy; resolution happens at
  first `get*` call). Cache keyed by `<scheme>:<full-path>` for the
  Config lifetime; cache MUST honour `SecretValue.expires_at` per
  ADR-0002 §3.
- `Config.load_from(..., eager_secrets=True)` opt-in flag to resolve
  every `SecretRef` at load time and surface backend errors at
  startup rather than at first request (ADR-0002 §3 "Resolution
  timing").
- `Config.refresh_secrets()` — drops the resolved-secrets cache;
  next `get*` re-resolves (ADR-0002 §3 "Forced refresh", manual
  rotation hook).
- `Config.snapshot(include_secrets=False)` — returns a deep-copy of
  the merged tree with every `SecretRef` replaced by `[MASKED]` and
  no backend round-trip; `include_secrets=True` resolves and applies
  field-name suffix masking (audit-mode opt-in per ADR-0002 §3
  trigger table).
- Lock-coalesced first-touch on the secrets cache — concurrent reads
  of the same cold key issue a single backend round-trip per
  ADR-0002 §Open-questions 3 RECOMMENDED.
- `[vault]` extra in `pyproject.toml` adds `hvac>=2.0,<3.0`.
- per-binding `adr/0001-vault-source.md` documenting `hvac` SDK
  choice + `[vault]` packaging + Phase 2 vs Phase 3 token-renewal
  boundary.

### Backwards compatibility

`${VAR}` Phase 1 syntax keeps working unchanged. `${secret:env:VAR}`
is semantically identical, so migration is a mechanical sed (no
breaking change for any existing consumer).

### Numbers

- 377 tests collected. Default run (no Vault dev server): 369 pass,
  8 skipped — 4 phase2_secrets_vault fixtures gated on
  `DAGSTACK_CONFORMANCE_VAULT_ADDR` and 4 runner-extension-required
  fixtures covered by binding-native unit tests instead.
- 12 phase2_secrets fixtures + 4 phase2_secrets_vault fixtures from
  `spec/conformance/` are wired into the conformance runner.

### Refs

- ADR-0002 §1 grammar, §2 SecretSource contract, §3 SecretRef +
  caching, §4 loader integration, §5 error reasons, §6 VaultSource.
- per-binding `adr/0001-vault-source.md`.

## [0.4.1] — 2026-04-27

First stable public release on pypi.org. Cumulative changes since 0.4.0:

- Translate comments and docstrings to English across `src/dagstack/config/`
  and `tests/` (rc2).
- Verified end-to-end on the pypi.org publish pipeline (rc1).

Non-functional relative to 0.4.0 — public API, runtime behaviour, and type
contracts unchanged. The corresponding documentation site
(config.dagstack.dev) is also English-first.

## [0.4.1rc2] — 2026-04-26

Translate Russian comments and docstrings to English across `src/dagstack/config/`
and `tests/`. Non-functional change — public API, runtime behaviour, and type
contracts unchanged. Motivation: lower the barrier for international adopters
(Russian docstrings were visible in IDE hover and on the github mirror).

## [0.4.1rc1] — 2026-04-25

First public-publish release candidate. Tests the pypi.org publish pipeline.

## [0.4.0] — 2026-04-23

Release tracking config-spec ADR v2.2 (pre-release quality hardening).
No breaking API changes, but several observable behaviour changes.

### New

- **`secrets_mask` module** — implements ADR v2.2 §6: source-of-truth suffix
  / prefix / exact patterns for auto-masking, `[MASKED]` placeholder.
  `is_secret_field()` and `mask_value()` are public helpers for
  custom diagnostics.

### Observable behaviour changes

- **`ConfigError.path` for array indices** (§4.2, §4.5): a nested validation
  failure inside a list field now returns `"section.servers[1].port"`
  instead of `"section.servers.1.port"`. The round-trip invariant with
  `has()` / `get()` works correctly.
- **YAML 1.2 strict mode** (§2): `Yaml12StrictLoader` removes YAML 1.1
  bool resolvers and registers only `true` / `false` (case-insensitive).
  Consequence: `yes: on` in YAML now parses as `{"yes": "on"}`
  (both strings), not `{True: True}`.
- **Secret masking in `ConfigError.details`** (§6): values of fields
  matching `_meta/secret_patterns.yaml` are replaced with `[MASKED]` in
  error messages. Previously `details` could contain a raw secret string.

### Conformance

- Submodule spec: `8cf2715` → `7ff2707` (ADR v2.2 merge).
- `conformance/` passes load-level fixtures: `ijson_safe_boundary`,
  `yaml_1_2_bool_literals`. The remaining v2.2 fixtures (getter-level,
  array-path) are covered by unit tests in `tests/test_v2_2_hardening.py`.

## [0.3.0] — 2026-04-23

Breaking release tracking config-spec ADR v2.1 (cross-binding conformance
tightening). Brings config-python into line with the spec on three
points the architect flagged as non-conforming.

### Breaking changes

- **`source_ids` is now a method, not a property** per ADR v2.1 §4.1. The
  shape across all three languages is unified as a method (Python `source_ids()`,
  TS `sourceIds()`, Go `SourceIDs()`). The value is computed from the current
  list of sources, and a copy is returned to guard against mutation.
  - Migration: `cfg.source_ids` → `cfg.source_ids()`.
- **`ConfigError.path` carries the full dot-notation path** per ADR v2.1 §4.5
  (Path preservation). Before: `get_string("a.b.c")` on a missing key
  returned `err.path="a"` (the first segment traversed before the failure).
  Now: `err.path="a.b.c"` — the full user-provided path on every reason
  (`missing`, `type_mismatch`, `validation_failed`). For `get_section` with
  a nested failure, `path = <section>.<field>` (concatenation of the
  section prefix with pydantic's `loc`).
  - Rationale: tooling-friendly diagnostics, log aggregation without
    accidentally collapsing distinct failure points under a single key.
- **Reverse coerce — `type_mismatch`, not `validation_failed`** per ADR v2.1
  §4.4 M1. A native `int/float/bool` in a `string` field of a pydantic
  schema now produces `ConfigError(reason=TYPE_MISMATCH)`, mirroring the
  §4.3 `getString` strict mode. This guards against silent
  `dimension: 768` → `"768"` coercion.
  - Migration: tests that expected `VALIDATION_FAILED` for this scenario
    must switch to `TYPE_MISMATCH`.

### Conformance

- Conformant with ADR-0001 v2.1 on §4.1 (source_ids), §4.4 (env-string
  coercion — pydantic handles this automatically), §4.5 (Path preservation).
- Conformance fixtures `path_preservation_missing_leaf` and
  `validation_nested_path` are covered by binding unit tests
  (`tests/test_config.py::TestGetSection`,
  `tests/docs_examples/test_reference_errors.py`).

## [0.2.0] — 2026-04-23

Breaking release tracking config-spec ADR v2.1 (wire clarifications).
Achieves byte-identical parity with `dagstack/config-go` v0.1.0 on the
`spec/conformance/` fixtures.

### Breaking changes

- **`get_string` is strict** per ADR v2.1 §4.3. The implementation up to
  v0.1.0 coerced `int` / `float` / `bool` to `str` (`42 → "42"`,
  `True → "true"`); now non-string values raise `TYPE_MISMATCH`.
  - Migration: for explicit conversion use `str(cfg.get(path))` or the
    specialized getters (`get_int`, `get_bool`, `get_number`).
- **Canonical JSON — whole-number floats emit integer form** per
  §9.1.1 / `_meta/canonical_json.yaml`. Before: `canonical_json_dumps(1.0)
  == "1.0"`; now: `"1"`. `-0.0` → `"0"` (integer form). Fractional
  floats keep their form. Parity with Go `strconv.FormatFloat('g')`.
  - Migration: if downstream parses canonical JSON as JSON — no change is
    required (`1` and `1.0` are equivalent under RFC 8259); if downstream
    treats it as text (byte-identical hash / golden file) — regenerate
    golden files.
- **`get_int` accepts whole-number floats inside the i-JSON safe range** per
  §4.3 clarification. `cfg.get_int("x")` for `{"x": 100.0}` now
  returns `100` (previously `TYPE_MISMATCH`). Fractional floats
  (`1.5`) and out-of-range values (`2**60`) are still rejected.
- **Source-level whole-number float → int normalization** for
  `YamlFileSource` and `JsonFileSource`. PyYAML `yaml.safe_load` and
  `json.loads` both return `100.0` as `float` and `100` as `int`; to
  make sources behave uniformly so that `cfg.get(path)` /
  `get_int` / a Pydantic section produce the same result regardless of
  whether the literal was `100` or `100.0`, we normalize at load time.
  Parity with `dagstack/config-go` v0.1.0 (the Go binding goes through
  `yaml.Node.Kind` for YAML and post-`json.Unmarshal` normalization).
  - Migration: if downstream relied on the difference between `100` and
    `100.0` at the level of the Python value's type — revisit your
    expectations. Semantically both values are now `int` in the safe range.
    Out-of-range whole-number floats and fractional floats are unaffected.
    No escape hatch (raw-float mode) in v0.2.

### Changed

- Submodule `spec/` → `09badaf` (ADR v2.1 + fixtures `whole_number_floats`,
  `null_parsing` + normative `_meta/coercion.yaml` /
  `_meta/canonical_json.yaml`).

### Added

- `get_int` docstring explicitly lists accepted types.
- `src/dagstack/config/_constants.py` — single `IJSON_SAFE_MAX = 2**53 - 1`,
  imported from `config.py`, `canonical_json.py`, `sources.py`.
- `_normalize_numbers` in `sources.py` — shared walker for YAML and JSON.
- 4 new unit tests:
  - `test_whole_number_float_emits_integer_form`
  - `test_out_of_range_whole_number_float_preserved`
  - `test_whole_number_float_in_safe_range_accepted`
  - `test_whole_number_float_outside_safe_range_rejected`

### Metadata

- 230 tests, 97% coverage, ruff + mypy strict clean.
- `dagstack/config-go` v0.1.0 and `dagstack/config-python` v0.2.0 — the
  first release of two bindings with bit-identical parity across 8
  conformance fixtures.

## [0.1.0] — 2026-04-19

First Phase 1 MVP release. Covers config-spec ADR-0001 v2.0 §1-§3 (wire format + layering + interpolation), §4 (API contract + getters + Pydantic typed sections), §7.2 (subscription API placeholder), §8.1/§8.2 Phase 1 MVP (`ConfigSource` Protocol + `YamlFileSource`/`JsonFileSource`/`InMemorySource`), §9.1 (conformance runner against spec golden fixtures).

### Added
- **Skeleton** — pyproject (`dagstack-config` PEP 420 namespace), CI workflows (dagstack-runner, 3.11/3.12/3.13 matrix), `spec/` submodule, publish workflow for Nexus `gx-pypi`.
- **Core primitives** — `ConfigError` + `ConfigErrorReason` enum; env interpolation parser `${VAR}` / `${VAR:-default}` (compose-style escape); deep merge (lists atomic replace); Canonical JSON serializer (RFC 8785 subset).
- **Public API** — `Config` class with `load/load_paths/load_from`, getters (`get_string/int/number/bool/list`, `has`, `get`), `get_section(path, PydanticModel)`, auto-discovery `.local.yaml` + `.${DAGSTACK_ENV}.yaml`, `Subscription` handle (Phase 1 inactive + warning).
- **Sources** — `YamlFileSource`, `JsonFileSource`, `InMemorySource` (Phase 1 MVP). Env interpolation runs before parsing (supports non-string positions).
- **Conformance** — runner reads `spec/conformance/manifest.yaml`, diffs against byte-identical `expected/*.json`; 6 fixtures (3 happy + 3 error cases) pass.

### Metadata
- 225 tests, 98% coverage, ruff + mypy strict clean.

[0.5.0]: https://github.com/dagstack/config-python/releases/tag/v0.5.0
[0.4.1]: https://github.com/dagstack/config-python/releases/tag/v0.4.1
[0.4.0]: https://github.com/dagstack/config-python/releases/tag/v0.4.0
[0.3.0]: https://github.com/dagstack/config-python/releases/tag/v0.3.0
[0.2.0]: https://github.com/dagstack/config-python/releases/tag/v0.2.0
[0.1.0]: https://github.com/dagstack/config-python/releases/tag/v0.1.0
