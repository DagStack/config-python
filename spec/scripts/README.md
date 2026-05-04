# scripts/

Tooling that drives the **cross-binding round-trip CI** (issue #10).

The conformance fixtures under `conformance/` define the expected
canonical JSON output for each input. Per-binding test suites already
verify that **their** output matches the canonical-JSON expected file.
The cross-binding round-trip adds a stricter check: that all three
bindings produce **byte-identical** canonical JSON for the same
input(s) — without depending on the spec's pre-baked
`expected/*.json` as oracle.

If `_meta/canonical_json.yaml` is normative and bindings implement it
correctly, three independent serialisers must agree. Diffing them
catches drift that per-binding fixtures might miss — a binding can
share the same bug with its expected file when that binding generated
the expected.

## Wrappers

| Script | Binding under test |
|---|---|
| `canonical_python.py` | `dagstack/config-python` (`canonical_json_dumps` + `deep_merge_all`) |
| `canonical_typescript.ts` | `dagstack/config-typescript` (`canonicalize` + `deepMergeAll`) |
| `canonical_go/main.go` (with `go.mod`) | `dagstack/config-go` (`LoadFrom` + `Snapshot` + `CanonicalJSON`) |

Each accepts **one or more** YAML inputs (one of
`conformance/inputs/*.yaml` or `conformance/errors/*.yaml`),
deep-merges them in order through the binding's public API
(priority — last wins, ADR-0001 §3), and writes the canonical bytes
to stdout. Multi-file invocation exercises layered semantics; single-
file invocation is the trivial case (deep-merge of one tree is
identity). No flags, no environment overrides — minimal surface so a
`diff` of two outputs is meaningful.

### All three wrappers bypass `${VAR}` interpolation

Round-trip CI verifies canonical-JSON byte-equality, not interpolation
semantics. To keep the diff meaningful across fixtures that contain
`${VAR}` placeholders without a corresponding env file in the runner,
each wrapper takes a path that **skips** the binding's interpolation
step:

- Python uses `deep_merge_all` directly on
  `yaml.load(text, Loader=Yaml12StrictLoader)` results — the binding's
  own YAML-1.2 strict loader, so `yes` / `no` / `on` / `off` decode as
  strings (not the YAML-1.1 booleans of PyYAML's default).
- TypeScript uses `deepMergeAll` directly on `parseYaml` results
  (the `yaml` npm package is YAML-1.2 by default).
- Go decodes YAML with `yaml.v3` and wraps each tree in a `DictSource`
  (which defaults to `Interpolate()==false`), then runs `LoadFrom` to
  perform the deep-merge through the binding's public loader.

The end-to-end semantics across all three wrappers becomes "YAML 1.2
parse → deep-merge → canonical JSON" — exactly what
`_meta/canonical_json.yaml` fixes. Interpolation is covered by each
binding's own conformance suite against the spec's `expected/*.json`.

A future improvement (tracked in a follow-up issue) is to export
`DeepMergeAll` from `config-go` for strict three-binding API parity,
which would let the Go wrapper drop its private YAML-decode hop.

## Local invocation

You need three sibling checkouts at the same level as `config-spec`:

```
~/projects/
├── config-spec/
├── config-python/
├── config-typescript/
└── config-go/
```

Then:

```bash
# Python (single-input)
PYTHONPATH=../config-python/src \
    python3 scripts/canonical_python.py \
        conformance/inputs/basic_interpolation.yaml > /tmp/py.json

# Python (layered, two inputs)
PYTHONPATH=../config-python/src \
    python3 scripts/canonical_python.py \
        conformance/inputs/layered_base.yaml \
        conformance/inputs/layered_override.yaml > /tmp/py-layered.json

# TypeScript (one-time setup: link the local package)
( cd ../config-typescript && npm link )
( cd scripts && npm link @dagstack/config && npm install yaml )
npx tsx scripts/canonical_typescript.ts \
    conformance/inputs/layered_base.yaml \
    conformance/inputs/layered_override.yaml > /tmp/ts-layered.json

# Go (one-time setup: a workspace pointing at the local checkout)
go work init scripts/canonical_go ../config-go
go run ./scripts/canonical_go \
    conformance/inputs/layered_base.yaml \
    conformance/inputs/layered_override.yaml > /tmp/go-layered.json

# Round-trip
diff /tmp/py-layered.json /tmp/ts-layered.json
diff /tmp/py-layered.json /tmp/go-layered.json
```

The CI workflow (`.gitea/workflows/cross-binding-roundtrip.yml`) does
the same setup in headless form and runs the diff over a matrix of
fixtures (single- and multi-file).

## Trigger limitation

The workflow lives in this repo and only runs on changes here (or on
the nightly cron). A canonical-JSON or deep-merge regression
introduced in a binding's own repo (`config-python` /
`config-typescript` / `config-go`) is **not** caught immediately —
the next spec PR or the next cron tick will surface it. Mitigations:

- each binding repo runs its own canonical-JSON suite against the
  spec's expected bytes (already in place);
- the nightly cron picks up upstream binding drift on the spec side;
- a binding-side PR can manually fire this workflow via
  `workflow_dispatch` (Gitea UI / API) when its change touches the
  canonical-JSON or merge surface.

This is the trade-off of the spec-side oracle pattern. Moving the
round-trip into each binding repo would duplicate setup three times
and lose the central comparison point.

## When CI fails

The diff output points to which canonical rule the divergent binding
violates. Common pitfalls:

- **Float representation** — shortest-round-trip rules differ between
  language stdlibs; `_meta/canonical_json.yaml` pins float formatting
  to a shortest-round-trip algorithm (Grisu / Ryū family); all three
  bindings MUST agree byte-for-byte on every fixture in the matrix.
- **`-0.0` normalisation** — must be emitted as `0`, not `-0`.
- **Sort order** — keys sorted by codepoint (UTF-8), not by locale.
- **Trailing newline** — none (the wrappers write to stdout without
  adding one; if a diff shows only a trailing `\n`, check the
  binding's helper).
- **YAML 1.2 quirks** — `~`, `null`, and missing values are all
  parsed as JSON `null`; bindings must not differ here.
- **Deep-merge semantics (multi-input)** — maps merge recursively,
  lists replace atomically; if a binding diverges only on the
  `layered` fixture, the bug is in its merge implementation, not
  canonical JSON.

## Adding a new fixture

1. Add the fixture under `conformance/inputs/<id>.yaml` (or several
   layer files for a layered fixture).
2. Generate `conformance/expected/<id>.json` via any one binding (or
   manually).
3. Add a matrix entry to `.gitea/workflows/cross-binding-roundtrip.yml`
   with `dir`, `fixture`, and a space-separated `inputs` list.
4. CI will fail on the next push if the bindings disagree.
