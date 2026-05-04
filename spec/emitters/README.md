# Emitters

Code generators that read `_meta/*.yaml` source-of-truth files and emit
language-specific constants for the bindings (`config-python`,
`config-typescript`, `config-go`, plus future Rust / Java / etc.).

## Why

Without emitters every binding hard-codes copies of the spec constants:

- `_meta/error_reasons.yaml` → `errors.py::ConfigErrorReason`,
  `errors.ts`, `errors.go`.
- `_meta/secret_patterns.yaml` → `_SECRET_SUFFIXES` / `_SECRET_PREFIXES` /
  `_SECRET_EXACT` in each binding.
- `_meta/coercion.yaml` → coercion-rule constants used by test fixtures.

Manual sync is a drift source. Real example: a v2.2 `masked_placeholder`
change had to be applied by hand to every binding — easy to miss one.

Emitters fix this:

1. `_meta/*.yaml` is the **single source of truth**.
2. Each binding has a `make sync-spec` target that runs the matching
   emitter and writes into a `_generated/` (Python) /
   `internal/generated/` (Go) / `src/generated/` (TypeScript) directory.
3. CI runs `make sync-spec && git diff --exit-code` — if the diff is
   non-empty, the binding is out of sync with the spec and the build
   fails.

## Layout

```
emitters/
├── README.md          # this file
└── emit.py            # the single Python entry point — emits all 3 langs
```

`emit.py` keeps the per-language assembly inline (plain f-strings, no
template engine) — the file is small enough that splitting layouts into
separate template files would only spread the logic across more files
without buying anything.

## Invocation

The emitter is a single Python script (Python is the lowest-friction
runtime; Go / Node bindings already require Python for `pre-commit` /
`pyenv` toolchains). Each binding shells out to it from its `Makefile`.

```bash
# From a binding repo, with `dagstack/config-spec` checked out as a
# git submodule under `vendor/config-spec/`:

python3 vendor/config-spec/emitters/emit.py \
    --lang python \
    --out src/dagstack/config/_generated/__init__.py

python3 vendor/config-spec/emitters/emit.py \
    --lang go \
    --out internal/generated/consts.go \
    --package generated

python3 vendor/config-spec/emitters/emit.py \
    --lang typescript \
    --out src/generated/index.ts
```

The emitter reads `_meta/*.yaml` from the same `config-spec` checkout
(`vendor/config-spec/_meta/...`), validates them, and writes the
emitted code to `--out`.

## What gets emitted

| Source | Emitted |
|---|---|
| `_meta/error_reasons.yaml` | `ERROR_REASONS` registry — list of `(name, value, description)` triples + a `ConfigErrorReason` string-enum for type-checked use. |
| `_meta/secret_patterns.yaml` | `SECRET_SUFFIXES`, `SECRET_PREFIXES`, `SECRET_EXACT` — three immutable lists. `MASKED_PLACEHOLDER` constant. |
| `_meta/coercion.yaml` | `COERCION_RULES` — single mapping of method-name to accept/reject sets, used by binding tests to assert rule parity. |

The conformance fixture machinery (`_meta/canonical_json.yaml`,
`_meta/types.yaml`) is **not** emitted — it is consumed by tooling
directly, not embedded as constants.

## Header / footer

Every emitted file starts with a header that:

- Marks the file as auto-generated (do not edit).
- Names the source `_meta/*.yaml` files and their `version:` fields.
- Names the spec commit SHA at the time of generation.

Bindings rely on the header for `git blame` discoverability and for
manual audit of which `_meta/` revision shipped with which binding
release.

## Adding a new language

1. Add a `--lang <name>` branch to `emit.py` that produces idiomatic
   code (frozen lists / enums / package-level constants) for that
   language.
2. Add a row to `_meta/types.yaml` for any new spec types the language
   exposes (so the registry stays up to date).
3. Open a PR against `dagstack/config-spec` with the new emitter +
   sample output verified against the spec's own test fixtures.
4. The new binding's repo then adds `make sync-spec` calling the new
   `--lang` and a CI drift check.

## Versioning

The emitter is **versioned in lockstep with the spec ADR revision** —
its output format changes only when the spec ADR makes a breaking
change to the `_meta/` schemas. A binding pinning `vendor/config-spec`
to a specific spec commit pins the emitter behaviour transitively.
