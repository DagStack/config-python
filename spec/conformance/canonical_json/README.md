# `conformance/canonical_json/`

Cross-binding wire-contract fixtures for canonical JSON serialisation
(ADR-0001 §9.1.1, `_meta/canonical_json.yaml`).

## `key_order_drift_witness.json`

Five-case normative fixture for object-key sort order per RFC 8785
§3.2.3. The five cases:

1. `ascii_then_latin1_then_cyrillic` — BMP-only baseline.
2. `emoji_after_bmp` — single supplementary key.
3. `supplementary_plane_pin` — two supplementary keys plus BMP.
4. `drift_witness_pua_vs_supplementary` — the case that distinguishes
   UTF-16 (RFC 8785) from UTF-32 (Python native) and from UTF-8 byte
   order (Go native).
5. `nested_recursion` — recursive sort, nested object literal.

The fixture is sourced from `dagstack/logger-spec`
`_meta/fixtures/canonical_json_key_order.json` and is byte-for-byte
identical so that canonical JSON behaves the same in both the config
and the logger subsystems.

## Runner integration

This fixture uses a five-case container format
(`cases: [{name, input, expected_wire}, ...]`). The existing
config-spec runner under `scripts/canonical_*` expects single-case
input/expected file pairs and does not yet decode the multi-case
container. Wiring the fixture into the matrix is tracked as a
follow-up — until then the file is the authoritative wire contract
the three bindings target when implementing the
`config-python` #25 / `config-typescript` #24 / `config-go` #28
fixes.

## Cross-references

- `_meta/canonical_json.yaml` — normative rules.
- ADR-0001 §9.1.1 — prose version of the rules.
- `logger-spec` ADR-0001 §14.1 / §14.5 — sister cross-binding
  contract; uses the same fixture.
