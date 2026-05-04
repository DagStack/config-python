# ADR-0001: YAML configuration with env interpolation

- **Status:** accepted
- **Revision:** 2.2 (2026-04-23) — pre-release quality hardening
- **Date:** 2026-04-23 (v2.2, v2.1), 2026-04-19 (v2.0), 2026-04-17 (v1.0)
- **Architect review:** ai-systems-architect (v1.0 — single round 2026-04-17; v2.0 — must-fix round 2026-04-19; v2.1 — must-fix round 2026-04-23; v2.2 — pre-release hardening round 2026-04-23)
- **Supersedes:** —
- **Revision history:**
  - v1.0 (2026-04-17) — initial decision, Python-first wording and examples.
  - v2.0 (2026-04-19) — rewritten language-agnostic: wire-format + API contract only, per-language bindings moved to separate spec repos. Added the `ConfigSource` abstraction (§8) with an adapter roadmap (file / etcd / Consul / Vault / HTTP / SQL / K8s), spec-distributed artefacts (§9) — conformance fixtures + source-of-truth `_meta/` + emitters following the `plugin-system-spec/emitters` pattern. Applied architect must-fix round: subscription-active signal, language-neutral error model, sync/async binding choice, `DAGSTACK_ENV` namespaced env var, canonical JSON formally fixed (RFC 8785 subset), cross-section atomicity made explicit, composed profiles deferred.
  - v2.1 (2026-04-23) — cross-binding conformance tightening: §4.1 explicitly fixes `source_ids()` as a method (not a property); §4.4 adds normative **env-string coercion for typed sections** (parallel with §4.3 / `_meta/coercion.yaml`) plus the reverse case (native int into a schema-string field → `type_mismatch`); §4.5 adds the **Path preservation** MUST for full dot-notation. Breaking for config-python v0.1.x/v0.2.x and config-go v0.1.x: bindings bump to v0.3.0 / v0.2.0 respectively. The bindings are **not yet published** in public repositories (PyPI / npm / Go vanity), so migration shims are not required.
  - v2.2 (2026-04-23) — pre-release quality hardening (no breaking API changes; clarifications + conformance additions):
    - §4.2 Path addressing — explicit rule for array indices in `ConfigError.path` (form `name[N]`, not `name.N`);
    - §4.4 — walker invariant (`get()` raw / `getSection()` post-coerce), origin-tagging Phase 1 warning, guidance for `ZodUnion` / `ZodDiscriminatedUnion` / `refine` / `transform` schemas;
    - §4.5 — `ConfigError.details` is **NOT** a wire contract warning + a positive statement about the programmatic check (reason + path);
    - §2 — YAML 1.2 strict mode is now MUST for all bindings (`yes` / `no` remain strings);
    - §6 — expanded list of secret suffixes plus new prefix/exact classes through `_meta/secret_patterns.yaml`; `masked_placeholder` fixed as `[MASKED]`;
    - `conformance/` — 4 new fixtures: `ijson_safe_boundary` (precision regression guard), `yaml_1_2_bool_literals` (strict mode), `error_path_array_index` (path form), `getter_raw_vs_section_view` (walker invariant load-level);
    - `_meta/secret_patterns.yaml` (new) — source-of-truth for scrub-list patterns;
    - Phase 2 deferred explicit: runtime subscription signatures may change — for example, the `subscription.on_change` callback may become async in all bindings, `ChangeEvent` may gain new fields (no API compatibility guarantee).
- **Related:** Backstage `@backstage/config` (prior art), Spring Boot `application.yml` (prior art), HashiCorp Viper (Go prior art), dagstack/plugin-system-spec ADR-0006 (discovery).

## Context

dagstack-based applications (pilot integrations and future products — written in Python, TypeScript, Go and other languages) are currently configured through flat env vars and ad-hoc loader modules in the host language. This approach does not scale:

- **Flat namespace**: `OPENAI_BASE_URL`, `QDRANT_URL`, `ANALYST_MODEL` — no structure, no way to group values logically.
- **No env separation**: dev / staging / production split through separate `.env` files without a merge strategy.
- **No runtime reload**: changing configuration requires restarting the process.
- **Secrets mixed with config**: API keys live in the same `.env` files as non-sensitive parameters.
- **Different languages, different formats**: a Python application reads `.env` through `python-dotenv`, a TypeScript application uses `dotenv`, a Go application uses `viper` / `envconfig`. There is no shared contract, and the operator changes configuration in different ways for each runtime.

**The goal of this ADR** is to fix a language-agnostic contract (wire format + API surface) so that the operator sees the same configuration model regardless of which language a particular plugin or application is implemented in. Per-language implementations (Python, TypeScript, Go, …) follow the contract, and their details live in separate spec repositories (`dagstack/config-python`, `dagstack/config-typescript`, `dagstack/config-go`).

### Prior art

- **`@backstage/config`** (Spotify, TypeScript): YAML + env interpolation + deep merge + hot-reload.
- **Spring Boot** (Java): `application.yml` + profiles + typed access through `@ConfigurationProperties`.
- **HashiCorp Viper** (Go): multi-format + env overrides + typed access through struct unmarshal.

All three ecosystems converged on the same model: a single YAML-like wire format, a layered merge strategy, and typed access through native language models. This ADR fixes the same model as a cross-language contract for dagstack.

## Decision

### 1. YAML as primary wire-format

```yaml
# app-config.yaml — base configuration

app:
  name: "order-service"

dagstack:
  plugin_dirs:
    - plugins/

database:
  host: ${DB_HOST:-localhost}
  port: ${DB_PORT:-5432}
  name: ${DB_NAME:-appdb}
  user: ${DB_USER}
  password: ${DB_PASSWORD}
  pool_size: 20

cache:
  url: ${REDIS_URL:-redis://localhost:6379/0}
  ttl_min: 15
  max_size_mb: 64

api:
  host: "0.0.0.0"
  port: 8080
  request_timeout_s: 30

auth:
  provider: ${AUTH_PROVIDER:-builtin}
  session_ttl_min: 60
```

YAML 1.2 (a subset compatible with YAML 1.1 parsers). The wire format is identical across languages — the same file is read consistently by Python, TypeScript and Go implementations. JSON is permitted as an equivalent wire format (YAML 1.2 is a superset of JSON) for environments without a YAML parser.

**YAML 1.2 strict mode (v2.2 normative).** Bindings **MUST** parse configs in
**YAML 1.2 strict** mode, not legacy YAML 1.1. Practical consequences:

- `yes` / `no` / `on` / `off` are **strings**, not booleans. Use `true` / `false`
  for boolean values.
- Leading-zero numeric literals (`0755`) are **decimal**, not octal. Use
  `0o755` for octal.
- `N`, `Y`, `y`, `n` are strings, not bool shortcuts.

**Per-binding guidance:**

- **Python (pyyaml)**: note that `version=(1, 2)` does **not** switch the Resolver to YAML 1.2 (pyyaml hardcodes the YAML 1.1 bool pattern in `resolver.py::BOOL_VALUES` regardless of the version header). Workarounds:
  - **Custom Resolver**: subclass `yaml.SafeLoader`, drop the YAML 1.1 bool tag patterns, keep only `^(true|false)$` (case-insensitive);
  - **Switch to ruamel.yaml** with `typ="safe"` + `version="1.2"` (strict mode works out of the box).
  Calling `yaml.load(..., version=(1, 2))` on its own is not enough; the conformance fixture `yaml_1_2_bool_literals` will fail.
- **TypeScript (`yaml` package ≥ 2.x)**: defaults to 1.2, OK.
- **Go (`gopkg.in/yaml.v3`)**: YAML 1.2 by default, OK.

The `yaml_1_2_bool_literals` conformance fixture verifies that `yes` parses as
a string, not a bool. If a binding needs legacy YAML 1.1 mode, that must be an
explicit opt-in through a separate source option, not the default behaviour.

### 2. Env interpolation syntax

```
${ENV_VAR}                → value of ENV_VAR, error if not set
${ENV_VAR:-default}       → value of ENV_VAR, or literal "default" if not set or empty
```

**Semantics:**

- Interpolation runs at **file load time**, before merge and before type coercion.
- The interpolated value is always a string. Type coercion happens at typed-access time (`getInt`, `getBool`, `getSection(schema)`).
- Escape literal `$`: `$$` → `$` (redundant in contexts without `${…}`, but reserved).
- An unresolved `${VAR}` without a default → `ConfigError` with the variable name and the path inside the config document.
- The default value is a literal string; nested `${…}` inside defaults is not interpolated (this keeps the parser simple).

### 3. Config layering and merge

```
app-config.yaml                    # base (checked into repo)
  + app-config.local.yaml          # local overrides (gitignored)
  + app-config.${DAGSTACK_ENV}.yaml     # env-specific (production, staging)
```

**Merge strategy:** deep merge of objects (maps). Arrays (sequences) are **replaced atomically**, not concatenated. To change a single element of an array, the override file must contain the full array.

**Resolution order** (lowest to highest priority):

1. `app-config.yaml` — base defaults.
2. `app-config.local.yaml` — developer overrides.
3. `app-config.{env}.yaml` — environment-specific (`{env}` is the value of the `DAGSTACK_ENV` env var; if missing, the layer is skipped).

**Namespacing rationale:** `DAGSTACK_ENV` is used instead of a generic `APP_ENV` / `NODE_ENV` / `RAILS_ENV` to avoid collisions in multi-framework deployments where a dagstack consumer runs alongside Spring / Next / Rails applications using the same host. Operators rolling out dagstack next to other frameworks do not fall into accidental cross-framework env shadowing.

**Composed profiles (deferred):** Spring Boot-style composition (`DAGSTACK_ENV=prod,us-east` activating two profiles at once) is **out of scope for Phase 1**. If a combination is needed, the operator lists files explicitly through `Config.loadFrom([...])` (§4.1). Composed profiles are considered a potential Phase 2 candidate but are not guaranteed.

The operator can add more layers through the explicit API (`Config.load([path1, path2, …])`); argument order equals priority order.

### 4. Config access API (language-agnostic contract)

The contract is described in **abstract notation** (TS-like pseudo-code for readability). Signatures define the *contract semantics*, not concrete language-level definitions. Per-language bindings (Python: Pydantic-based, TypeScript: zod-based, Go: struct-tag-based, etc.) implement the contract idiomatically for their language:

- **Sync vs async.** Operations marked as potentially I/O-bound (`load`, `loadFrom`, `source.load`, `source.watch`, `config.reload`) are implemented in the binding's native idiom. Python and TypeScript may pick async (`async def load(...)`, `async load(...): Promise<Config>`); Go uses sync with an `error` return; Rust uses `async fn` or blocking, at the binding's choice. Each binding fixes its choice in its per-language ADR and in the conformance runner.
- **Error signalling.** A single *structural* `ConfigError` type (§4.5) — the contract is over **fields** (`path`, `reason`, `details`), not runtime representation. Python, TypeScript and Java raise / throw an exception; Go returns `(value, error)`; Rust returns `Result<T, ConfigError>`. The type name in each binding is conventional, but the fields are mandatory.
- **Callback semantics.** Callbacks (§7.2) may be sync or async depending on the binding. The loader always treats callbacks as fire-and-forget (see §7.2).

#### 4.1. Loading

```
Config.load(path: string): Config                  # Phase 1 shortcut — file-based
Config.load(paths: string[]): Config               # explicit file layering
Config.loadFrom(sources: ConfigSource[]): Config   # Phase 2+ — arbitrary sources (see §8)
```

The file-based forms (`load(path)` / `load(paths)`) are semantic sugar over `loadFrom([YamlFileSource(...)])`. Auto-discovery of `app-config.local.yaml` and `app-config.${DAGSTACK_ENV}.yaml` next to the base file is a mandatory part of the file loader contract (see §3).

For each source the loader runs: `source.load()` → ConfigTree → env interpolation (if the source marks tree leaves as interpolatable) → deep merge of all trees in source order → return a `Config` object.

The binding picks a sync or async signature (see the §4 preamble).

**Optional extension methods.** A binding may add **idiomatic helper methods**
on top of the `Config.*` contract, provided they do not contradict its
semantics and do not introduce wire-visible behaviour. Recommended extension
methods:

- `snapshot(): ConfigTree` — deep-copy of the merged tree for dump or
  comparison; already implemented in Go (`cfg.Snapshot()`), available in
  Python / TypeScript on demand.
- `close(): void` (or the binding idiom: `__exit__` / `AsyncDisposable` /
  Go `Close()`) — release source resources if Phase 2+ introduces long-living
  sources (etcd stream, fsnotify watchers). In Phase 1 it is a no-op; in
  Phase 2+ it forwards `source.close()` to all managed sources.
- `source_ids(): string[]` — list of `id` values of the registered sources in
  priority order, for diagnostics / structured logs. **OPTIONAL in Phase 1,
  SHOULD in Phase 2+** in all bindings. Forms: `source_ids()` (Python
  snake_case), `sourceIds()` (TypeScript camelCase), `SourceIDs()` (Go
  PascalCase + initialism). **A method, not a property** — this emphasises
  that the value is computed from the current source list rather than read
  from a cached field; that gives a consistent call-site pattern across
  bindings.

Extension methods are **not exercised** by the conformance runner, but their
presence / absence is recorded in the per-binding README.

**Bound on the extension space (normative).** Extensions beyond the
`snapshot` / `close` / `source_ids` list **MUST** go through an ADR amendment
in `config-spec` before they can land in a binding's public API. This
prevents binding-specific divergence (one binding adding `Config.toJSON()`,
another adding `Config.reload()` "as idiomatic", and cross-binding tooling
breaking). Internal methods with a `_` prefix (Python), `private` modifier
(TypeScript) or lowercase name (Go) are not public and the rule does not
apply to them.

#### 4.2. Path addressing

Config values are addressed through **dot-notation paths**:

- `database.host` — object → field.
- `cache.region.host` — nested path.
- `dagstack.plugin_dirs[0]` — array element.
- `dagstack.plugin_dirs[*]` — the whole array as a single value (with `get` / `getList`).

Escaping a key that contains a dot uses backslash: `labels.kubernetes\.io/zone`. A per-language binding may offer alternatives (for example, passing a list of path segments), but dot-notation is mandatory.

**Array indices in error paths (v2.2 normative).** `ConfigError.path` (§4.5)
**MUST** use the same notation for array indices — `name[N]`, not `name.N`.
Example: when calling `getSection("database", DatabaseConfig)` whose schema
contains `servers: list[Server]`, and a validation error arises at
`servers[0].port`, `err.path` is `"database.servers[0].port"`, not
`"database.servers.0.port"`. This guarantees:

1. **Round-trip** — the path from an error can be passed verbatim to
   `has(path)` / `get(path)` for inspection.
2. **Tooling** — `dagstack-config-lint` and IDE plugins can parse the path
   unambiguously, without "is this an index or a key" heuristics.

Native schema validators (pydantic, zod, yaml.v3) return integer segments in
their `loc` / `path`. The binding **MUST** wrap integer segments in `[N]`
when serialising to `ConfigError.path`, rather than concatenating them with
a dot. Conformance: fixture `error_path_array_index` (§9.1).

#### 4.3. Primitive getters

> **Breaking change note.** The coercion rules table below fixes the strict
> behaviour of `getString` (no coercion of int / float / bool to string).
> Bindings at the v0.1.x level (the lenient `config-python`) are
> **non-conforming**; upgrading to this revision of the ADR requires a major
> bump (`v0.2.0`) plus a tracking issue in each binding repo. New bindings
> (Go v0.1.0+) are strict from the start.

```
config.has(path): boolean
config.get(path): Value               # raw value (any type)
config.getString(path, default?): string
config.getInt(path, default?): int
config.getNumber(path, default?): float/decimal
config.getBool(path, default?): boolean
config.getList(path): Value[]         # raw list
```

**Semantics:**

- Without `default`: missing key → `ConfigError(path, reason=missing)`.
- With `default`: missing key → `default`.
- Type mismatch → `ConfigError(path, reason=type_mismatch, expected, actual)`.

**`default?` signature — per-language idiom.** In the pseudo-syntax above
`default?` is written as an optional positional / keyword parameter. The
concrete idiomatic mapping is up to the binding:

- **Python** — keyword argument with a sentinel default: `config.get_int("port", default=8080)` / without a default — `config.get_int("port")`. The implementation uses a `_MISSING = object()` sentinel to distinguish "default not provided" from `default=None`.
- **TypeScript** — optional positional parameter: `config.getInt("port", 8080)`. No sentinel needed — `arguments.length >= 2` is enough.
- **Go** — **a separate method with the `Default` suffix**: `cfg.GetInt("port")` without a default, `cfg.GetIntDefault("port", 8080)` with one. Go has no idiomatic optional parameters (variadic plus a `len(args)` check is an anti-pattern in stdlib); a pair of methods models the spec semantics exactly. Symmetrical for `Get{String,Number,Bool}Default`.
- **Rust** — `Option<T>` in the return + `.unwrap_or(default)` at the call site (not in the getter signature): `cfg.get_int("port").unwrap_or(8080)`.

All four forms are **semantically equivalent** to the spec text; the
conformance runner checks behaviour, not signature shape.

**`has(path)` semantics.** Returns `true` if and only if the path exists in the merged tree. **An explicit `null` / `None` / YAML `~` counts as "present"** — `has` checks the presence of a key, not the truthiness of its value. The behaviour matches `dict.__contains__` / `map.has` in host languages and clearly distinguishes `has` from `getString(path, "").truthy()`.

**Coercion rules (normative).** A binding only applies coercion according to the rules below; any other (target_type, actual_value) pair is `type_mismatch`:

| Target type | Accepts |
|---|---|
| `getString` | only native `string`. `getString` does **NOT** coerce int / float / bool / null / list / map into a string — type conversion is left to the consumer through an explicit call. Motivation: a typical mistake is to pass `EMBEDDINGS_DIMENSION=768` as a string into a URL or SQL parameter without an explicit cast; a strict `getString` makes the conversion mandatory and visible in code. |
| `getInt` | native `int` / `int64`; a string matching `^-?\d+$` (env interpolation); **whole-number `float64`** (no fractional part, within the i-JSON safe range, 2^53-1). Accepting whole-number floats as ints is needed for JSON sources: Go's `encoding/json` and JavaScript's `JSON.parse` always decode numbers as floats. A float with a fractional part → `type_mismatch`. |
| `getNumber` | native int family; native float32/64; a string matching the ECMAScript float literal (`^-?\d+(\.\d+)?([eE][-+]?\d+)?$`). |
| `getBool` | native `bool`; a string `true\|false\|yes\|no\|1\|0` (case-insensitive). |
| `getList` | only a native sequence; strings are NOT split by a separator. |

String representations from env interpolation are coerced by the same rules. Wire-format value handling is on the `source.load()` side, not on the getter side.

The mirrored rule for serialisation lives in §9.1.1 (whole-number floats are emitted in integer form).

#### 4.4. Typed section access

```
config.getSection(path, schema): TypedObject
```

`schema` is a native language model with validation support:

- Python → Pydantic `BaseModel` subclass.
- TypeScript → zod schema (`z.object({...})`).
- Go → struct with `yaml:"…"` / custom tags.
- Java → a `@ConfigurationProperties`-style POJO.

The binding guarantees: the section is extracted, coerced to the schema, and validated. Validation failure → `ConfigError(path, validation_details)`.

**Env-string coercion for typed sections (normative).** Values that come from
`${VAR}` interpolation are always strings at the `source.load()` level (§2).
At `getSection` time the binding **MUST** apply the same coercion rules as
described in §4.3 / `_meta/coercion.yaml` before passing the value to the
native schema validator:

- If a schema field is declared as `int` (Python `int` / TS `z.number().int()` / Go `int`) — the string value is matched against `^-?\d+$` and converted to int; otherwise → `ConfigError(reason=validation_failed, path=<section.field>, ...)`.
- The same applies to `number` (ECMAScript float literal regex) and `bool` (`true|false|yes|no|1|0`, case-insensitive).
- This means that `port: "${DB_PORT:-5432}"` in YAML against an int `port` field in a schema is a **valid** config: the binding coerces `"5432"` → `5432` before the validator runs.
- **Reverse direction (native value → schema string).** If a schema field is
  declared as `string` and the YAML value is a native int / float / bool, that
  is a `ConfigError(reason=type_mismatch, path=<section.field>, ...)`,
  mirroring §4.3 `getString` strict mode. An env-substituted string in a
  `string` field is accepted as is, without any processing. This protects
  against silently turning `dimension: 768` (int in YAML) into `"768"` (str
  in the pydantic / zod model) — such conversion bugs must fail visibly.

**Motivation.** Without this rule, env-substituted values work in primitive
getters (`getInt("db.port")`) but break under typed reads (`getSection`),
which produces cross-binding divergence (Python / pydantic coerces
automatically; TS / zod and Go / yaml.v3 do not). Pinning the requirement in
the spec brings every binding to the same behaviour.

**Walker invariant (v2.2 normative).** Coercion is applied **only** inside
the `getSection` path, never inside `get` / `getString` / `getInt` / `has`.
In other words:

- `config.get(path)` **MUST** return the raw value from the merged tree
  (with no post-coerce walker processing). If YAML had
  `port: "${DB_PORT:-5432}"` and the env substituted `"5432"`,
  `get("database.port")` returns the string `"5432"`, not the integer 5432.
- `config.getInt(path)` applies the primitive coercion of §4.3.
- `config.getSection(path, schema)` applies the schema-aware coercion of the
  current subsection and returns a typed object.

The binding **MUST** keep this separation explicit — it must not introduce
side effects in `get` and must not cache a post-coerce tree across calls.
Conformance: fixture `getter_raw_vs_section_view` (§9.1).

**Implementation guidance** (non-normative):
- **Python** — Pydantic coerces automatically (an `int` model accepts `"5432"`); no explicit step is required, but the binding MUST verify that `strict_mode` does not disable coercion for known fields.
- **TypeScript** — after env interpolation and before passing the tree to the zod schema, walk the merged tree (`ConfigTree` / `map<string, unknown>`) and convert env-substituted strings using the regexes in `_meta/coercion.yaml`. Alternative: rewrite the schema through a `z.coerce` helper — but that loses the env-origin information.
- **Go** — after env interpolation and before native schema validation, walk the merged `config.Tree` (`map[string]any`) with the same coercion logic. The choice between a walker and a `yaml.Node` AST is up to the binding.

**Origin tagging (Phase 1 heuristic — important warning).** Full origin
tagging (`{ value, from_env: bool }` on every leaf of the merged tree) is
**deferred** to Phase 2+. In Phase 1 bindings apply coercion to **any**
string that matches the regex of the corresponding schema target type,
regardless of whether the string came from an env substitution or from a
YAML native literal.

**Consequences for Phase 1:**

1. `port: "5432"` (a native string in YAML, not env-substituted) in an int
   schema field **will still be coerced** to int 5432. This is redundant
   coercion but harmless in practice: if the user declared the field as
   int, they expected an integer; the string `"5432"` is a clear YAML typo
   (the author forgot to remove the quotes).
2. `version: "42"` (a native string) in a field
   `z.union([z.string(), z.number()])` is **ambiguous**: the walker does
   not know which branch of the union to prefer; it leaves the value as a
   string by default (see ZodUnion guidance below).
3. To guarantee that a specific field is **not** coerced, declare it as
   `str` / `z.string()` / `string` in the schema; the §4.4 reverse case
   prevents the walker from touching a string-typed field.

In Phase 2+ (once explicit origin tagging arrives) coercion will be limited
to env-origin values only. In the Phase 1 contract this is **SHOULD**, not
`MUST`, to keep the pilot bindings simple. Technically all three native
YAML parsers (pyyaml, js-yaml, yaml.v3) **can** expose AST / Node access
with mark / line info — but an intrusive walker over the AST makes the code
heavier.

**Exit criteria for the Phase 2 upgrade to MUST**: a cost-benefit analysis
on a pilot binding (scope: walker complexity + perf impact + test
coverage); on an upgrade decision — a spec amendment v2.3 or v2.4 with an
explicit migration note and a breaking version bump for the bindings.

**ZodUnion / ZodDiscriminatedUnion / refine / transform guidance (v2.2,
TypeScript-specific).** The walker zod-introspection in v0.2.0 only
recognises the normative schema types (`ZodObject`, `ZodNumber`,
`ZodBoolean`, `ZodOptional`, `ZodDefault`, `ZodNullable`, `ZodArray`).
Unknown types are passed through:

- `z.union([z.string(), z.number()])` — the walker does **not** coerce
  inside; it forwards the value as is to the zod validator. Zod picks the
  first matching branch; an env-substituted string `"5432"` stays a
  string.
- `.refine()` / `.transform()` — the walker does not try to interpret the
  semantics; it forwards the raw value, and refine / transform run as the
  user declared.
- `z.discriminatedUnion(...)` — same as the union case.

**Recommendation for users** (TypeScript): if you need guaranteed coercion
for a field inside a union, wrap it with `z.preprocess((x) => …, z.number())`
or use `z.coerce.number()` in the specific union branch.

**Equivalent rules for Python / Go** — pydantic `Union[str, int]` and Go
`interface{}` / duck-typed fields: the walker does not coerce into a union
and leaves the call to the native validator. This is by design: without an
explicit discriminator the binding cannot safely choose a type.

**Conformance**: covered by the `env_coerce_in_getsection` fixture under
`conformance/errors/` (requires a runner extension for `getSection`-level
checks — see the §9.1.2 follow-up).

**Isolation rule:** plugins read only their own section (`config.getSection("my_plugin", MyPluginSchema)`). Reading other plugins' sections is an anti-pattern (see §9 Open questions).

#### 4.5. Error model

The contract fixes the **structural fields** of the error, not its runtime representation. Structure:

```
ConfigError {
  path:     string       # full dot-notation path (§4.2) where error occurred
  reason:   ConfigErrorReason  # enum, values fixed in _meta/error_reasons.yaml (§9.2)
  details:  string       # human-readable message
  source_id?: string     # which ConfigSource (§8) raised the error, if applicable
}
```

**Path preservation (normative).** `path` **MUST** contain the **full
dot-notation path** of the user's call, without truncating to the first
segment. Array indices use the `[N]` notation (§4.2). This guarantees:

1. The diagnostic message points directly at the YAML line that operators or
   developers will inspect.
2. Log aggregation systems can filter by `path` without accidental matches
   between distinct failure points sharing the same first segment.
3. Tooling (`dagstack-config-lint`, CI checks) can parse the path back into
   a YAML location for inline annotations.
4. The error's path can be passed verbatim to `has(path)` / `get(path)` for
   inspection — the round-trip invariant.

**Examples:**

| Call | Scenario | Expected `path` |
|---|---|---|
| `getString("a.b.c")` | the key is missing at level `a.b.c` | `"a.b.c"` (not `"a"`) |
| `getInt("database.pool_size")` | the value `"twenty"` does not match `^-?\d+$` | `"database.pool_size"` |
| `getSection("database", DatabaseConfig)` | the schema validator rejects `pool_size` | `"database.pool_size"` (section root + field path) |
| `getSection("api", ApiConfig)` | nested `api.ratelimit.window_s` is invalid | `"api.ratelimit.window_s"` |
| `getSection("db", Schema)` | `servers[0].port` is invalid in the schema | `"db.servers[0].port"` (array index as `[N]`, not `.0`) |
| `load("missing.yaml")` | the file is missing | `""` (source-level error, no path) |

**Binding implementation note.** For Python / TS / Go typed sections: native
schema validators (pydantic, zod, yaml.v3) return the **relative path** to
the offending field within the section (for example, `["pool_size"]`). The
binding **MUST** concatenate it with the section prefix (`"database"`) into
a full dot-notation path — otherwise downstream tooling loses context.

**Conformance**: covered by the `path_preservation_missing_leaf`,
`validation_nested_path` and `error_path_array_index` fixtures under
`conformance/errors/` (requires a runner extension for getter- and
getSection-level checks — see the §9.1.2 follow-up).

**`ConfigError.details` is NOT a wire contract (v2.2 normative).**
The `details` field is **human-readable**, binding-specific phrasing. Users
**MUST NOT** match on substrings inside `details`; such usage is **not**
covered by semver compatibility. The binding may rephrase the message
between patch releases.

Example: Python emits `"Pydantic validation failed: ..."`, Go emits
`"field X failed 'min' validation"`, and TypeScript emits
`"schema validation failed: ..."`. All three are semantically equivalent,
but comparing them by substring is an anti-pattern. For diagnostics, treat
`details` as an opaque log string.

**Wire contract (for programmatic checks):**

- `reason` — enum from `_meta/error_reasons.yaml`;
- `path` — dot-notation with array indices in the `[N]` form (§4.2, §4.5 Path preservation);
- `source_id` (optional) — URI-like identifier from `ConfigSource.id`.

Any other parsing of `ConfigError` fields (including substring checks on
`source_id`) is at your own risk and is **not semver-stable**.

`ConfigErrorReason` is an enum with fixed values (the source of truth is `_meta/error_reasons.yaml`): `missing`, `type_mismatch`, `env_unresolved`, `validation_failed`, `parse_error`, `source_unavailable`, `reload_rejected`.

**Idiomatic implementation per language:**

- **Python**: `class ConfigError(Exception)` with the listed attributes; raise on error.
- **TypeScript**: `class ConfigError extends Error` with fields; throw on error (async-friendly via `try/await`).
- **Go**: `type ConfigError struct { ... }` implements `error`; functions return `(value, error)`; wrappers go through `errors.As` for type checks. The type name in each binding may shorten under stutter avoidance (see the table below).
- **Rust**: `struct ConfigError` + `enum ConfigErrorReason`; functions return `Result<T, ConfigError>`.
- **Java**: `class ConfigException extends RuntimeException` with fields; throw on error.

The binding records its choice in its per-language ADR. Fields (`path`, `reason`, `details`, `source_id`) are mandatory in all bindings and carry identical values for the same error event (verified by the conformance fixtures, §9.1).

**Stutter avoidance for public type names (normative).**

**One-line rule:** a binding may drop the `Config` prefix from public type
names if its package / module / namespace is already named `config`,
provided single-form consistency holds (see below).

For each public type whose spec name has the `Config` prefix
(`ConfigError`, `ConfigErrorReason`, `ConfigSource`, `ConfigTree`), a
binding **may drop the `Config` prefix** if the implementation's package /
module / namespace already contains the word `config` in its import path
and the full name `<package>.<Type>` would create cosmetic stutter. The
correspondence between the spec name and the binding name remains
idiomatically equivalent and cross-binding parity is preserved at the
semantic level: types that play the same role map one-to-one between
bindings.

| Spec form | Python `dagstack.config.*` | TypeScript `@dagstack/config` | Go `go.dagstack.dev/config` |
|---|---|---|---|
| `ConfigError` | `ConfigError` | `ConfigError` | `config.Error` |
| `ConfigErrorReason` | `ConfigErrorReason` | `ConfigErrorReason` | `config.ErrorReason` |
| `ConfigSource` | `ConfigSource` | `ConfigSource` | `config.Source` |
| `ConfigTree` | `ConfigTree` | `ConfigTree` | `config.Tree` |

**Single-form rule (RFC 2119).** A binding **MUST** export **exactly one**
name for each type from the mapping table above. Type aliases, re-exports
and deprecated shims **MUST NOT** ship in the public API alongside the
canonical form — except for a window of exactly one major release during a
formal migration (a `CHANGELOG.md` entry plus an explicit
`DeprecationWarning` / `@deprecated` JSDoc / Go build tag). The chosen form
is fixed in the per-binding ADR / CHANGELOG and does not change after a
major release without a version bump and a migration note.

**Machine-checkable source of truth.** The mapping table above is a
**prose mirror** of the normative artefact `_meta/types.yaml` (added in a
separate commit in this same PR). `_meta/types.yaml` carries one record
per public spec type: the canonical spec form plus the per-binding rendered
form. Emitters and the conformance runner use the file as the source of
truth; per-binding READMEs reference the column for their platform. A new
binding (Rust, Java) **must** add a row to `_meta/types.yaml` through an
ADR amendment in `config-spec`, not in its own repository.

**Cross-reference §8.2.** See §8.2 for source adapter naming — the same
freedom for an idiomatic alias under preserved semantics is justified
there (`InMemorySource` / `DictSource`). The rule is the same: idiomatic
flexibility is allowed, but a single form per binding is mandatory.

**Why we don't enforce a strict cross-binding name.** In Go idiom the
linters (`golint` / `revive` / `staticcheck`) flag `config.ConfigError` as
`stutter` — public stdlib packages follow the same rule (`context.Context`,
`errors.New`, `http.Handler`). Forcing the Go binding to share a common
cross-language name would create visual noise at every consumer for no
real semantic value — identifier names are read in the context of the
package path, and `config.Error` in Go is semantically equivalent to
`ConfigError` in Python / TypeScript. The stutter-avoidance rule does
**not** apply to wire values (`reason` enum values, dot-notation path
syntax, canonical JSON output) — those names are fixed byte-for-byte in
`_meta/*.yaml` and are not subject to binding-specific mimicry.

### 5. Packaging model

This repository (`dagstack/config-spec`) is **wire format + API contract only, no implementation**. Per-language implementations live in separate spec repositories:

- `dagstack/config-python` — Python binding (Pydantic-based), published to PyPI as `dagstack-config`.
- `dagstack/config-typescript` — TypeScript / Node binding (zod-based), published to npm as `@dagstack/config`.
- `dagstack/config-go` — Go binding (struct-tag-based), Go module `github.com/dagstack/config-go`.
- Additional bindings (Java, Rust, etc.) are added as consumers appear.

Each per-language repo has its own set of ADRs describing implementation-specific decisions (YAML library choice, schema framework, packaging deadlines) and conformance tests against the golden fixtures from `dagstack/config-spec` (see §8).

### 6. Secrets handling (Phase 2+)

**Phase 1:** secrets via `${ENV_VAR}` interpolation. Fields whose names match
the patterns from `_meta/secret_patterns.yaml` (the source of truth) are
masked automatically in diagnostic output (`snapshot()`,
`ConfigError.details`, logs). The value is replaced with `[MASKED]`; the key
(field name) stays unchanged — otherwise the diagnostic context is lost.

**Patterns** (the full list lives in `_meta/secret_patterns.yaml`):

- **Suffix match** (case-insensitive): `_key`, `_secret`, `_token`, `_password`, `_passphrase`, `_credentials`, `_credential`, `_auth`, `_api_key`, `_access_key`, `_private_key`.
- **Prefix match** (case-insensitive): `api_key`, `api_token`, `secret`, `password`, `private_key`, `access_token`, `bearer`.
- **Exact match** (case-insensitive): `api_key`, `apikey`, `password`, `passwd`, `pw`, `token`, `secret`, `credentials`.

**Check order**: suffix → prefix → exact (OR semantics; the order inside
each list is not part of the wire contract — a match in any list means
mask).

**Masked placeholder**: `[MASKED]` (normative). Earlier (v2.0 / v2.1) the
spec required `***` — v2.2 standardises on `[MASKED]` as more
self-documenting. Bindings v0.3.0 (Python), v0.2.0 (TS / Go) must update
their masking logic and re-run snapshot tests after the submodule upgrade
to v2.2.

The patterns are fixed in `_meta/secret_patterns.yaml`; a per-language
binding **does not extend** the list without an ADR change. For native
pydantic / zod secret types (for example, `pydantic.SecretStr`) a binding
MAY apply additional local rules, but **only on top** of the normative
list, never instead of it.

**Phase 2:** two complementary mechanisms:

1. `${secret:name}` interpolation syntax for inline references in YAML.
2. Pluggable **source adapters** (§8): `VaultSource`, `AwsSecretsManagerSource`, `K8sConfigMapSource`, etc. — secrets backends as first-class sources in `Config.loadFrom([...])`.

When to choose which: `${secret:name}` for single-value references, source adapters for wholesale loading of a section from a secret manager. A detailed spec lives in a separate ADR.

### 7. Hot-reload and subscriptions

#### 7.1. Reload triggers

Config can be reloaded by one of these triggers:

1. **Source-driven push** — a source with a `watch()` capability notifies the loader of changes itself (file via OS notifications inotify / kqueue / ReadDirectoryChangesW; etcd / Consul via the native watch API; HTTP via long-polling / SSE).
2. **Explicit reload** — through the API `config.reload()` (an admin endpoint in the application calls this method).
3. **Polling** — for sources without a push model (HTTP with If-Modified-Since). The period is a constructor parameter on the source.

**Phase 1:** sources without watch capability, `config.reload()` available as an explicit API. Callback subscriptions (§7.2) can be registered without errors but never fire (no source delivers change events).

**Phase 2:** file watch for `YamlFileSource`, push watch for `EtcdSource` / `ConsulSource`, polling for `HttpRemoteSource`, inotify-aggregated watch for `K8sConfigMapSource`.

#### 7.2. Subscription API

Two subscription levels — **path-level** (a single value) and **section-level** (a typed object via a schema):

```
subscription := config.onChange(path: string, callback: (ChangeEvent) => void): Subscription
subscription := config.onSectionChange(path: string, schema: Schema<T>, callback: (T, T) => void): Subscription
```

**`Subscription` handle:**

```
Subscription {
  unsubscribe():   void      # idempotent; after call, callback guaranteed not to fire
  active:          boolean   # true iff at least one registered source supports watch() AND matches path scope
  inactive_reason: string?   # diagnostic message when active=false (e.g., "no watch-capable source registered")
  path:            string    # echoed subscription path for introspection
}
```

`active=false` is **not an error** but a contractual signal that the callback will never fire under the current source configuration. The loader **must**, when registering a subscription:

1. Check whether at least one source supports `watch?`.
2. If not — set `active=false`, `inactive_reason="no watch-capable source registered"`, and emit a structured warning into the binding's diagnostic channel (Python: `logging.warning`; TS: `console.warn` or an injected logger; Go: a structured log entry with `level=warn`). Warning code: `subscription_without_watch`; payload: `path`, the registered `source_ids` lacking watch capability.
3. If there are watch-capable sources but none of them covers the subscribed path scope (for example, an `EtcdSource` is restricted to the prefix `/dagstack/prod/cache/*`, but the subscription is on `database.host`) — `active=false`, `inactive_reason="no source covers this path"`.

This protects against silent bugs: a developer who subscribed in Phase 1 (where watch is not yet implemented) is not left in the dark.

**`ChangeEvent` structure:**

```
ChangeEvent {
  path:       string        # dot-notation, exact key that changed
  old_value:  Value | null  # null if key was absent before
  new_value:  Value | null  # null if key was removed
  source_id:  string        # which ConfigSource delivered the change
  change_id:  string        # monotonic identifier for the reload (same for all events in one reload batch)
  timestamp:  ISO-8601
}
```

**Semantics:**

- **Path matching:** `onChange("database.host", cb)` fires on a change of exactly that key; `onChange("database", cb)` fires on any change inside the `database.*` subtree (prefix match, one event per change = one callback call).
- **Section callback** receives two validated schema instances (prev, next). If validation fails after a reload, subscribers are NOT notified and the change is rejected as a whole (see §8.4).
- **Cross-subscription atomicity (must-hold).** If schema validation fails for *any* registered section subscription after a reload, the change is rejected globally: **neither path-level nor section-level subscribers** (including subscribers on keys / sections unrelated to the validation failure) are notified. The atomic swap is "all or nothing", not per-subscription. Formally this follows from §8.4.
- **Initial value:** subscription does NOT deliver the "current" value immediately; the callback fires only on subsequent changes. For "current + subscription", use the pattern `current := config.getSection(...); subscription := config.onSectionChange(...)`.
- **Ordering:** within a single `change_id` the order of callbacks across subscribers is implementation-defined but stable across reloads. Across `change_id`s the order is FIFO.
- **Atomicity:** all callbacks for a single `change_id` see a merged tree from one snapshot (atomic swap inside the loader, §8.4).
- **Callback invocation mode.** Callbacks are invoked **fire-and-forget**. A callback can be sync or async (binding-specific). The loader does not wait for a callback to finish before invoking the next, and does not wait for all callbacks before completing the reload. If the binding uses async callbacks (Python asyncio, TS promises), the loader schedules them without awaiting.
- **Callback errors:** any error (sync exception, rejected Promise, panic, returned `error`) is caught by the loader, logged with `source_id + path + change_id + subscription_path`, does not interrupt delivery to other subscribers, and does not roll back the change. Binding-specific: for async callbacks the loader subscribes to the Promise / Future and logs the rejection.
- **Unsubscribe:** `subscription.unsubscribe()` is idempotent; after unsubscribe the callback is guaranteed not to be called, even for an already-running reload batch. The implementation MUST guarantee that an unsubscribe called from inside a callback of the same subscription completes the current call cleanly while cancelling any pending ones.

#### 7.3. Example

```
# Typical use case — recreate the database pool when host or password changes
subscription = config.onSectionChange("database", DatabaseSchema, (old, new) => {
    if old.host != new.host or old.password != new.password:
        db_pool.reconnect(new.host, new.password)
})

if not subscription.active:
    logger.warning("Database config subscription inactive: " + subscription.inactive_reason)

# Shutdown
subscription.unsubscribe()
```

#### 7.4. Dev vs production

Dev mode: watch is on by default (fast iterative loop). Production: watch through an explicit opt-in on the source constructor (`YamlFileSource(path, watch=true)`) or only through an admin-API reload trigger (security-sensitive environments). The spec for production hardening (audit trail, rate-limited reload calls, rollback API) is a separate ADR (Phase 2+).

### 8. Source adapters

Config is not required to come from a file. The contract carves out a **`ConfigSource`** abstraction — any source that yields a `ConfigTree` (a nested map of scalars / sequences / maps).

#### 8.1. ConfigSource contract

```
ConfigSource {
  id:          string       # human-readable identifier for diagnostics
                            # e.g., "yaml:app-config.yaml", "etcd://prod/dagstack/sample-app"
  load():      ConfigTree   # returns parsed tree; binding chooses sync/async idiom
  interpolate: boolean      # hint to loader: apply ${VAR} to string leaves (true for YamlFileSource)
  watch?(callback: (tree) => void): Subscription  # optional — push-based reload (§7 Phase 2+)
  close?():    void         # optional cleanup (file handles, network sockets)
}
```

**Sync vs async.** `load()` and `watch()` are potentially I/O-bound. The binding picks an idiom for its language: Python / TS may implement them as `async` (Promise / coroutine), Go as sync with an `error` return, Rust as `async fn` or blocking. The choice is fixed in the per-language ADR; mixing sync and async source adapters in a single `loadFrom()` is binding-specific.

`ConfigTree` is a language-neutral representation: nested maps, sequences, scalars (string / int / float / bool / null). Type coercion still happens at the `Config.get*` / `getSection` level, never inside the source.

#### 8.2. Source adapter roadmap

| Adapter | Purpose | Phase | Read-only / mutable |
|---|---|---|---|
| `YamlFileSource(path)` | local YAML file (base + layers) | 1 | read-only |
| `JsonFileSource(path)` | JSON equivalent (YAML 1.2 superset) | 1 | read-only |
| `InMemorySource(tree)` — idiomatic alias `DictSource(tree)` in Go (see below) | programmatic / tests | 1 | read-only |
| `EtcdSource(uri, prefix)` | centralised distributed KV | 2 | read-only at load; mutable via `watch()` |
| `ConsulSource(uri, prefix)` | Consul KV | 2 | same as etcd |
| `VaultSource(path)` | HashiCorp Vault — secrets (§6) | 2 | read-only |
| `HttpRemoteSource(url)` | Backstage-style HTTP-served config | 2 | read-only; poll-based watch |
| `SqlDatabaseSource(dsn, table)` | RDBMS (Postgres, MySQL) | 3 | mutable, watch via LISTEN/NOTIFY (Postgres) or polling |
| `ZookeeperSource(uri, znode)` | ZK-based deployments | defer until demand | same as etcd |
| `AwsSecretsManagerSource(arn)` | AWS-managed secrets | 2 | read-only |
| `K8sConfigMapSource(ns, name)` | mounted ConfigMap (file) + watch via Kubernetes API | 2 | read-only at load |

**Convention:** URI-like `id` (`etcd://…`, `consul://…`, `vault://…`, `k8s://…`) for logs and diagnostics.

**`InMemorySource` / `DictSource` — per-binding idiom.** The canonical name is
`InMemorySource` (Python, TypeScript). In Go the idiom uses a
container-typed name instead of a descriptive one — `DictSource` (by
analogy with `map[string]any` ≈ dict) with a `NewDictSource(tree Tree)`
constructor, symmetrical to `YamlFileSource` / `JsonFileSource`, which are
also "<format/type>Source" without a `FromFile` prefix. The cross-binding
semantics is identical — an in-memory read-only tree with no
interpolation by default — so both names are normatively equivalent (see
§4.5 stutter avoidance — the same rule of idiomatic freedom under
preserved semantics).

#### 8.3. Source composition

```
config := Config.loadFrom([
    YamlFileSource("app-config.yaml"),                  # 1. base defaults
    YamlFileSource("app-config.local.yaml"),            # 2. dev overrides (skipped if missing)
    EtcdSource("etcd://prod/dagstack/sample-app"),      # 3. runtime tuning
    VaultSource("secret/dagstack/sample-app"),          # 4. secrets (highest priority)
])
```

The order of the list is the priority order (lowest to highest). Merge uses the same deep merge as §3.

#### 8.4. Watch semantics (Phase 2+)

If sources support `watch()`, the loader subscribes to each one and on an event:

1. Recomputes a **candidate merged tree** (using cached trees from other sources plus the new tree from the watched source).
2. **Validation phase:** the loader runs validation for **all** registered `onSectionChange(path, schema, ...)` subscriptions against the candidate tree. If at least one validation fails, the candidate is **rejected as a whole**:
   - The previous tree stays active (no atomic swap happens).
   - **Neither path-level nor section-level subscribers** (none, even for unrelated sections) are notified.
   - A `reload_rejected` warning is emitted into the diagnostic channel with `change_id`, `source_id`, `failed_paths`, `validation_details`.
   - The reload does not retry automatically — the next change event from a source triggers a new attempt.
3. **Swap phase:** if validation passes, the loader performs an **atomic swap**: the merged tree is replaced by the candidate as a single unit. Concurrent readers calling `config.get*()` / `config.getSection(...)` see either the full prev snapshot or the full next snapshot.
4. **Notification phase:** after a successful swap the loader notifies subscribers (fire-and-forget, see §7.2 Callback invocation mode). All callbacks for one `change_id` see the same snapshot.

**Atomicity contract (summary):**

- **Tree level:** it is impossible to read a partially applied merge (a reader sees either prev or next).
- **Subscription level:** a reload is "all or nothing". Any rejection in the validation phase = zero notifications, even for unrelated subscriptions. This protects against race conditions where, say, a `database` subscriber would receive a rejected change just because the `cache` section failed validation.
- **Source level:** if several sources emit changes simultaneously, the loader may either coalesce them into one `change_id` or process them in sequence — implementation-defined, but always under the global atomicity contract.

#### 8.5. Minimum viable set (Phase 1)

In Phase 1 only **file-based sources** are required (`YamlFileSource`, `JsonFileSource`, `InMemorySource` for tests). The `ConfigSource` abstraction is already in the API, but additional adapters are Phase 2+. This gives forward compatibility: migration from `Config.load("app-config.yaml")` to `Config.loadFrom([...])` does not break existing applications.

### 9. Spec-distributed artefacts

`dagstack/config-spec` distributes three categories of artefact that binding repos pull in as a submodule (or vendor through `git subtree`):

#### 9.1. Conformance fixtures (`conformance/`)

Language-neutral golden fixtures for testing bindings:

- `conformance/inputs/*.yaml` — input configs (base + layers, with env interpolation).
- `conformance/env/*.env` — env vars per test.
- `conformance/expected/*.json` — the expected result after merge + interpolation, serialised to **Canonical JSON** (see §9.1.1).
- `conformance/errors/*.yaml` + `conformance/errors/*.expected.json` — expected `ConfigError` structures (`path` + `reason` + the relevant `details` fields).
- `conformance/manifest.yaml` — a machine-readable test registry: paths to inputs, env, expected, error — with `description`, `tags` (`phase1`, `layering`, `interpolation`, `errors`, `subscriptions`) for filtering.
- `conformance/runner.md` — the unified runner spec: how a binding discovers fixtures, the run order (env set → load → serialise actual to canonical JSON → diff against expected), the expected CLI (`<binding>-conformance <path-to-spec>`), exit codes.

##### 9.1.1. File encoding and Canonical JSON

**Encoding for all spec files** (yaml, json, md, env): **UTF-8, LF line endings, no BOM, no trailing newline**. The spec-repo CI validates this through a pre-commit hook.

**Canonical JSON** for `expected/*.json`:

- Built on **RFC 8785 (JSON Canonicalization Scheme)** subset: sorted object keys (lexicographic, UTF-8 code-point order), no whitespace except inside strings, integers without a decimal point (`1`), floats in ECMAScript shortest round-trip representation, NaN / Infinity forbidden.
- **Whole-number floats are emitted as integers** (`100.0 → "100"`, `-0.0 → "0"`) — RFC 8785 §3.2.2.3 via ECMAScript `ToString(Number)`. Bindings whose JSON serialiser emits `100.0` by default (Python `json.dumps`, Ruby `JSON.generate`) **must** post-process the output until it matches the integer form. The mirrored rule for parsing is in §4.3 (a whole-number `float64` is accepted as int by `getInt`).
- **Breaking change note.** This rule introduces incompatibility with `config-python` v0.1.x (which emitted `100.0` and `0.0`). Consumers comparing canonical-JSON hashes between binding versions get different hashes for the same merged tree. A coordinated major bump of all bindings plus consumers like `logger-spec` body-hash is required.
- **Differences from the full RFC 8785**: UTF-8 encoding is forced (RFC 8785 is neutral) and a trailing newline is forbidden (RFC 8785 does not regulate it).
- Reference: `_meta/canonical_json.yaml` — exact rules and edge-case examples (unicode, large numbers, null ordering); this file is the source of truth when prose disagrees with it.
- A binding must have a serialiser that produces bit-identical output for the same input; the conformance fixtures verify this through a `git diff --exit-code` equivalent.

Every binding must run these fixtures in CI and match the golden output. A divergence is either a bug in the binding or, more rarely, a proposal for a spec update via an ADR amendment.

#### 9.2. Source-of-truth metadata (`_meta/`)

Machine-readable definitions from which code generators emit language-native code:

- `_meta/error_reasons.yaml` — the `ConfigError.reason` enum (values: `missing`, `type_mismatch`, `env_unresolved`, `validation_failed`, `parse_error`, …). The source of truth for constants in all bindings.
- `_meta/interpolation.yaml` — regex patterns for `${VAR}` / `${VAR:-default}`, type-coercion rules (int / bool), escape sequences.
- `_meta/secret_patterns.yaml` — suffix patterns for fields whose values are masked automatically in diagnostics (`*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`, `*_PASSPHRASE`, `*_CREDENTIALS`).
- `_meta/source_adapters.yaml` — registry of adapters (§8.2) with URI scheme and expected capabilities (watch yes / no, mutable yes / no). Used for `Config.loadFrom()` dispatch registration and human-friendly error messages.
- `_meta/canonical_json.yaml` — formal rules for Canonical JSON serialisation (§9.1.1): key ordering, number representation, UTF-8 encoding, forbidden constructs. The source of truth when prose in the ADR disagrees.
- `_meta/coercion.yaml` — type-coercion rules for `config.get*` methods (§4.3): which input types `getInt` / `getString` / `getBool` / `getNumber` / `getList` accept, accept / reject decisions for whole-number floats, JSON-source normalisation. The source of truth on cross-binding ambiguity.

#### 9.3. Emitters (`emitters/`)

Code generators following the `dagstack/plugin-system-spec/emitters/` pattern — they read `_meta/*.yaml` and emit language-native artefacts:

| Emitter | Output | Phase |
|---|---|---|
| `python_pydantic.py` | `_generated/{error_reasons,interpolation,secret_patterns,source_adapters}.py` (enums + constants + regex) | 1 |
| `typescript_zod.py` (Python emitter, `.ts` output) | `_generated/*.ts` with `as const` enums and zod regexes | 1 |
| `go_const.py` | `_generated/*.go` with `const (...)` blocks and `regexp.MustCompile` | 1 |

**Emitter conventions follow `plugin-system-spec` exactly:**

1. An emitter is a standalone script that takes a path to a spec checkout and an output dir.
2. Output is **deterministic**: the same input yields bit-equal output (for the `git diff --exit-code` CI gate in binding repos).
3. Generated files carry a header:
   ```
   # AUTO-GENERATED. Do not edit.
   # Source: dagstack/config-spec @ <git-sha>
   #         _meta/<file>.yaml
   ```
4. CI gate in `config-spec`: `emitters/python_pydantic.py --check /tmp/out` validates that generation does not fail on the current `_meta/*.yaml` (smoke test without writing output).
5. Consumer workflow (in a binding repo):
   ```
   git submodule add git@github.com:dagstack/config-spec.git spec
   make emit
   # → spec/emitters/python_pydantic.py generates into src/dagstack/config/_generated/
   git diff -- src/dagstack/config/_generated/     # must be empty (CI gate)
   ```

**What is NOT emitted** (intentional):

- The `Config` class / interface / API itself — written by hand in each language, idiomatically.
- The typed `getSection(schema)` implementation — depends on the native schema framework (Pydantic / zod / struct tags).
- YAML parser integration — library choice lives in the per-language ADR.

Only those artefacts where **bit-identical values across languages are critical** are emitted: enums, regex patterns, suffix lists, the adapter registry.

#### 9.4. Adding a new language

Reuse the playbook from `plugin-system-spec`:

1. Add `emitters/<lang>_<framework>.py` (deterministic, header-compliant).
2. Create a `dagstack/config-<lang>` binding repo with a `Makefile` `emit` target that runs the emitter.
3. CI: `git diff --exit-code` after `make emit` plus a conformance run against `conformance/**`.
4. Update `_meta/source_adapters.yaml` — mark which adapters the binding implements (even if the spec defines them as Phase 2+, a binding may implement only a subset).

## Consequences

### Positive

- **Cross-language UX parity** — the operator sees one configuration model in Python / TS / Go dagstack applications.
- **Wire-format stability** — `app-config.yaml` is portable across languages without changes.
- **Structured config** — nested objects, lists, typed access through the native schema framework.
- **Environment separation** — base + local + env-specific without duplication.
- **Golden fixtures** — conformance tests guarantee that all bindings behave the same on edge cases (missing env, type coercion, deep merge).
- **Migration path** — env vars keep working through `${ENV_VAR}` interpolation without structural changes.

### Negative

- **Spec maintenance burden** — changes to the contract touch every binding; we need a process for synchronised updates.
- **YAML parser dependency on the binding side** — `PyYAML` / `js-yaml` / `gopkg.in/yaml.v3`, etc. Library choice lives in the per-language ADR.
- **Double schema declaration for cross-language plugins** — a plugin that exists in two languages (for example, Python + TS) must support two schemas (Pydantic + zod). Mitigation: if needed, one can be generated from the other (JSON Schema as an intermediate format) — but that is out of scope for Phase 1.
- **Migration cost** — existing projects migrate from per-language ad-hoc loaders gradually.

### Migration path from ad-hoc per-language config

1. Create `app-config.yaml` with current defaults.
2. Replace direct env lookups (`os.getenv` / `process.env` / `os.Getenv`) with `Config` API calls.
3. Env vars keep working through `${ENV_VAR}` interpolation — no change for operators.
4. Gradually structure the config: group related parameters under a common key, declare a typed schema per section.

## Explicitly out of scope (Phase 1)

### Deferred to Phase 2+

- `${secret:name}` interpolation — Phase 2 (§6).
- Non-file source adapters (etcd, Consul, Vault, DB, HTTP, K8s, cloud secrets) — Phase 2+ (§8.2). The `ConfigSource` abstraction is already in the API, but the implementations are deferred.
- Watch capability for sources (inotify, etcd watch, long-polling, K8s watch API) — Phase 2 (§7 + §8.4). The subscription API (`onChange` / `onSectionChange`) is already in the contract in Phase 1, but in Phase 1 callbacks do not fire because no source delivers change events; the loader emits a `subscription_without_watch` warning (§7.2).
- Composed profiles (Spring Boot-style `DAGSTACK_ENV=prod,us-east`) — a potential Phase 2 candidate, not guaranteed. For now there is a single active env; composition runs through an explicit `loadFrom([...])`.

### Forever out of scope (explicit non-goals)

- **Tenant-scoped configuration** — this is a product concern. It is implemented in a separate repo (`dagstack/tenancy`, future) which uses `config-spec` as a lower layer but does not change its contract.
- **`DAGSTACK_CONFIG_*` env-var flat-to-nested overrides** (à la the `SPRING_` prefix in Spring Boot) — nice to have; if needed, it lands as a source adapter (`EnvVarOverrideSource`) without changes to the ADR.
- **A full YAML schema specification for config values (root-level JSON Schema dump)** — the per-language binding generates one when needed; the spec does not impose a shape.
- **Cross-language schema codegen for pluggable user schemas** (Pydantic ↔ zod ↔ Go struct for arbitrary `getSection` models) — plugins declare their schema in their own language. Only shared constants / regexes are emitted (§9.3).
- **Encrypted-at-rest config files** (SOPS / age / sealed-secrets) — landed as a source adapter (`SopsSource(path, key)` / `AgeSource(...)`), without changes to the contract. The out-of-scope note is an explicit signal that the ADR is ready for this use case through an extension to §8 rather than core-spec edits.
- **Feature flags** — a separate domain (LaunchDarkly / Unleash / a dagstack/feature-flags subsystem of our own). Config is for static / semi-static application parameters; feature flags are targeted runtime behaviour switches with targeting, percentage rollouts and experiments. They do not mix.

## v2.1 backlog (should-consider, does not block v2.0)

Suggestions from the architect collected during the v2.0 review. They are not applied now to keep Phase 1 scope tight; they are revisited after the first pilot binding integration and feedback collection.

1. **Adapter roadmap as data, not prose** — shrink §8.2 to 3-4 canonical examples (`YamlFileSource`, `EtcdSource`, `VaultSource`, `HttpRemoteSource`); the full registry lives only in `_meta/source_adapters.yaml` with a lifecycle (`phase: 1|2|3`, `stability: experimental|stable|deprecated`). This keeps ADR prose from drifting away from the registry.
2. **`ConfigSource` vs `SecretSource` separation** — secrets backends have substantially different semantics (lazy resolution, rotation, leases); mixing them in the §8.3 example may produce a leaky abstraction. Consider two sub-contracts with a marker capability (`kind: "tree" | "secret_bag" | "lazy_secret"`) or two parallel interfaces.
3. **Emitters scope minimisation** — for the smallest `_meta/error_reasons.yaml` and `_meta/interpolation.yaml` (few values) the emitter overhead is disproportionate. Consider: in Phase 1 emit only `source_adapters` + `secret_patterns`; the rest stays as documentation plus manual copy with PR review.
4. **Plugin scope seam** — `config.scope(path): Config` as a first-class API. A plugin gets a "sub-config view" with the root rebased to its section and cannot accidentally read other sections. This does not enforce isolation but provides a client pattern (§4.4).
5. **Observability hooks in the loader** — `loader.onReload(cb)`, `loader.metrics` (reload counts, validation failures, source load latencies), OpenTelemetry integration as a convention. The operator sees the health of the config loader, not just data-plane events.
6. **Dry-run reload** — `config.dryReload(): DiffReport` returns the merged-tree diff plus the list of subscriptions that would fire plus the validation status (including failures). It reduces the fear of hot-reload in production.
7. **Validation CLI** — `dagstack-config validate ./app-config.yaml` as spec-distributed tooling. The operator validates a config before deploy. Cheap to implement on top of the same loader.

## Open questions

1. **Config inheritance between plugins**: plugin A reading plugin B's config section — **no, isolation by default**. If plugins need shared config, it lives in a common section (for example, `dagstack.shared.*`), and both plugins read it explicitly.
2. **Path syntax for array indexing in deeply nested structures** (`a.b[0].c[1].d`) — supported by the contract, but a per-language binding may defer the implementation until the first request.
3. **Source priority for mutable adapters**: if `EtcdSource` has higher priority than `YamlFileSource` and an etcd watch delivers a key deletion — should the value from YAML resurface? (Current intuition: yes, the merge is recomputed honestly; pin formally in §8.4.)
4. **Coalescing strategy for simultaneous change events from several sources** (§8.4): merge into a single `change_id` or process in sequence? Both are legally sound but have different observability properties. Decision: after the first reference binding integration.
