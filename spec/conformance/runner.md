# Conformance Runner

`conformance/` holds golden fixtures for testing per-language bindings (`config-python`, `config-typescript`, `config-go`, …). Each binding pulls `config-spec` in as a git submodule and runs the fixtures in its own CI.

## Directory layout

```
conformance/
├── manifest.yaml          # Test registry (see below)
├── runner.md              # This file
├── inputs/                # Input YAML configs (possibly multi-layered per test)
├── env/                   # Env vars per test (KEY=VALUE format)
├── expected/              # Expected canonical JSON after merge + interpolation
└── errors/                # Error-case inputs + expected error specs
```

## manifest.yaml

```yaml
version: "1.0"
tests:
  - id: <stable identifier>
    description: <human readable>
    tags: [<tag>, <tag>]       # interpolation | layering | errors | happy | ...
    inputs:                    # Input files in layering order (lowest priority first)
      - inputs/<file>.yaml
    env: env/<file>.env | null # Optional env file; null if no env required
    expected: expected/<file>.json     # For happy path
    expected_error:                    # For an error case
      reason: <enum value>             # from _meta/error_reasons.yaml
      path: <dot-notation>             # path at which the error was raised
      source_id_pattern: <regex>       # optional, when checking the source_id field
```

Exactly one of `expected` / `expected_error` is required; specifying both is a manifest error.

## Env file format

```
# comments allowed
KEY=value
ANOTHER_KEY=another value
```

Blank lines and lines starting with `#` are ignored. No shell escaping — values are literal (space-containing values do not need to be quoted).

## Expected format (happy path)

The file `expected/<test_id>.json` is the **canonical JSON** (§9.1.1) result after:
1. Read inputs in list order.
2. Apply env interpolation to each input (`${VAR}` / `${VAR:-default}`).
3. Parse as YAML 1.2 / JSON.
4. Deep-merge in list order (lowest priority first).
5. Serialise the merged tree to canonical JSON.

The binding must produce **byte-identical** output. `git diff --exit-code` against `expected/` is the CI gate.

## Expected format (error case)

The file `errors/<test_id>.expected.json` is canonical JSON with this structure:

```json
{"reason":"<value>","path":"<path>"}
```

Optional fields: `source_id` (string), `source_id_pattern` (regex for lax matching when the source_id contains an absolute path).

## Binding runner responsibilities

The binding MUST provide a CLI or test integration that:

1. Parses `manifest.yaml`.
2. For each test:
   - Reads inputs (multiple, ordered).
   - Reads the env file (if present) and passes it as an env mapping (does NOT overwrite the process environment).
   - Calls the binding's primary load API:
     - Python: `Config.load_from([YamlFileSource(p, env=env_map) for p in inputs])`.
     - TS: `Config.loadFrom([new YamlFileSource(p, {env: envMap})])`.
     - Go: equivalent.
   - Happy path: serialise the result through the binding's canonical JSON serialiser; diff against `expected/<id>.json` byte-identically.
   - Error case: assert that ConfigError was raised; check that `reason` / `path` match `expected_error`.
3. Exit code: 0 if all pass, 1 on any failure.
4. Output: human-readable diff on failure (path within the test, expected vs. actual first 20 chars).

## Adding a new fixture

1. Create `inputs/<id>.yaml` + an optional `env/<id>.env`.
2. Compute the expected canonical JSON by hand (read ADR §9.1.1 rules, double-check against an existing binding for sanity).
3. Add an entry to `manifest.yaml`.
4. Run all bindings' runners — every one must pass. If a binding fails, that is either a bug or a spec ambiguity — discuss in the PR.

## Versioning

- `manifest.version: "1.0"` — incompatible changes (field renames, removal, semantic changes) bump major.
- Adding new tests / tags / optional fields is backward-compatible and stays on the same version.
- On a major bump the previous manifest version is supported for N releases (grace period); bindings can read v1.0 and v2.0 until deprecation.
