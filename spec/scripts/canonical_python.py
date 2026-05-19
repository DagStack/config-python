#!/usr/bin/env python3
"""Cross-binding round-trip CI: Python wrapper.

Invocation:
    PYTHONPATH=/path/to/config-python/src \
    python3 scripts/canonical_python.py <fixture.yaml> [<layer.yaml> ...]

Reads one or more YAML / JSON fixtures (one of the files under
`conformance/inputs/` or `conformance/errors/`), deep-merges them in
order through the binding's `deep_merge_all` (priority: last wins —
ADR-0001 §3), and emits canonical JSON of the merged tree to stdout.

Multi-file invocation exercises layered semantics — the only path that
covers `Config.load_paths` deep-merge across maps + atomic list-replace.
Single-file invocation is a strict subset (deep_merge_all of a one-
element list is identity), so the wrapper handles both cases uniformly.

The binding under test exposes
`dagstack.config.canonical_json.canonical_json_dumps` since v0.4 and
`dagstack.config.merge.deep_merge_all` since v0.2.

The output is byte-stable per spec §9.1.1 (`_meta/canonical_json.yaml`).
A cross-binding round-trip CI compares this output byte-for-byte
against the Go and TypeScript wrappers' output for the same fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

try:
    from dagstack.config.canonical_json import canonical_json_dumps
    from dagstack.config.merge import deep_merge_all
    from dagstack.config.sources import Yaml12StrictLoader
except ModuleNotFoundError as exc:  # pragma: no cover — diagnostic only
    print(
        f"ERROR: dagstack.config not importable — set PYTHONPATH to "
        f"include the binding's src/ directory.\n"
        f"  detail: {exc}",
        file=sys.stderr,
    )
    sys.exit(2)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: canonical_python.py <fixture.yaml> [<layer.yaml> ...]",
            file=sys.stderr,
        )
        return 2
    trees = []
    for path_arg in argv[1:]:
        text = Path(path_arg).read_text(encoding="utf-8")
        # The binding parses with `Yaml12StrictLoader` — PyYAML default
        # is YAML 1.1, where `yes`/`no`/`on`/`off` decode as booleans;
        # ADR-0001 v2.2 §2 mandates 1.2 (those tokens stay strings). The
        # wrapper must mirror the binding here, otherwise the round-trip
        # would catch the wrapper's YAML-1.1 drift instead of a real
        # canonical-JSON disagreement (caught precisely this in CI run
        # 1607: `yaml_1_2_bool_literals` diverged Py↔TS until this fix).
        loaded = yaml.load(text, Loader=Yaml12StrictLoader)  # noqa: S506 — strict 1.2 loader
        # YAML-empty file → None — deep_merge_all expects a mapping.
        trees.append(loaded if loaded is not None else {})
    merged = deep_merge_all(trees)
    sys.stdout.write(canonical_json_dumps(merged))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
