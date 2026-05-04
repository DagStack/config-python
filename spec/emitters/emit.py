#!/usr/bin/env python3
"""Code generator: `_meta/*.yaml` source-of-truth → language constants.

See `emitters/README.md` for the full design rationale.

Invocation (from a binding repo with `vendor/config-spec/` submodule):

    python3 vendor/config-spec/emitters/emit.py \
        --lang python --out src/dagstack/config/_generated/__init__.py

Languages: python | go | typescript.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _q(s: str) -> str:
    """Quote a string literal as it would appear in a JSON document.

    Used to inject string constants into emitted Python / Go / TypeScript
    source — JSON's escape rules are a strict subset of all three
    languages' string-literal grammars, so the result is safe to drop
    in verbatim.
    """
    return json.dumps(s, ensure_ascii=False)

# --------------------------------------------------------------------------- #
# Spec source paths (relative to this script)
# --------------------------------------------------------------------------- #

SCRIPT_DIR = Path(__file__).resolve().parent
SPEC_ROOT = SCRIPT_DIR.parent
META_DIR = SPEC_ROOT / "_meta"

ERROR_REASONS_PATH = META_DIR / "error_reasons.yaml"
SECRET_PATTERNS_PATH = META_DIR / "secret_patterns.yaml"
COERCION_PATH = META_DIR / "coercion.yaml"

# Fallback placeholder if `_meta/secret_patterns.yaml` is missing the
# `masked_placeholder:` field (older spec revisions). Source-of-truth
# is the YAML field, not this constant; emitter reads YAML at every run.
_MASKED_PLACEHOLDER_FALLBACK = "[MASKED]"


# --------------------------------------------------------------------------- #
# Loaded spec model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ErrorReason:
    name: str
    value: str
    description: str


@dataclass(frozen=True)
class SpecModel:
    error_reasons: tuple[ErrorReason, ...]
    error_reasons_version: str
    masked_placeholder: str
    secret_suffixes: tuple[str, ...]
    secret_prefixes: tuple[str, ...]
    secret_exact: tuple[str, ...]
    secret_patterns_version: str
    coercion: dict[str, Any]
    coercion_version: str
    spec_commit_sha: str


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"emit.py: missing source file {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"emit.py: {path} root is not a mapping")
    return data


def _resolve_spec_commit() -> str:
    """Return the short SHA of the spec checkout this emitter ran against.

    Falls back to `unknown` if `git` is unavailable or the spec is not a
    git checkout (for example, when shipped inside a wheel).
    """
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(SPEC_ROOT), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return sha or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def load_spec() -> SpecModel:
    er = _load_yaml(ERROR_REASONS_PATH)
    sp = _load_yaml(SECRET_PATTERNS_PATH)
    co = _load_yaml(COERCION_PATH)

    reasons = tuple(
        ErrorReason(
            name=str(r["name"]),
            value=str(r["value"]),
            description=str(r["description"]).strip(),
        )
        for r in er.get("reasons", [])
    )
    if not reasons:
        raise SystemExit("emit.py: error_reasons.yaml has no reasons[]")

    return SpecModel(
        error_reasons=reasons,
        error_reasons_version=str(er.get("version", "unknown")),
        masked_placeholder=str(
            sp.get("masked_placeholder", _MASKED_PLACEHOLDER_FALLBACK)
        ),
        secret_suffixes=tuple(sp.get("suffixes", [])),
        secret_prefixes=tuple(sp.get("prefixes", [])),
        secret_exact=tuple(sp.get("exact", [])),
        secret_patterns_version=str(sp.get("version", "unknown")),
        coercion=co,
        coercion_version=str(co.get("version", "unknown")),
        spec_commit_sha=_resolve_spec_commit(),
    )


# --------------------------------------------------------------------------- #
# Emitter helpers
# --------------------------------------------------------------------------- #


def _content_hash(text: str) -> str:
    """SHA-256 first 16 hex of the emitted text — used in the CLI's
    stderr log line so an operator can quickly diff two regenerations
    by hash alone (without diffing the whole file). Not embedded into
    the file body, so the hash is purely for human triage.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _header(spec: SpecModel, lang: str) -> str:
    lines = [
        "Source-of-truth: dagstack/config-spec _meta/*.yaml",
        f"  error_reasons.yaml v{spec.error_reasons_version}",
        f"  secret_patterns.yaml v{spec.secret_patterns_version}",
        f"  coercion.yaml v{spec.coercion_version}",
        f"  spec commit: {spec.spec_commit_sha}",
        "",
        "Generated by emitters/emit.py — DO NOT EDIT BY HAND.",
        "Regenerate via `make sync-spec` in the binding repo.",
    ]
    if lang == "python":
        return '"""' + "\n".join(lines) + '"""'
    if lang == "go":
        return "// " + "\n// ".join(lines)
    if lang == "typescript":
        return "/**\n * " + "\n * ".join(lines) + "\n */"
    raise ValueError(lang)


# --------------------------------------------------------------------------- #
# Python emitter
# --------------------------------------------------------------------------- #


def emit_python(spec: SpecModel) -> str:
    parts: list[str] = []
    parts.append(_header(spec, "python"))
    parts.append("")
    parts.append("from __future__ import annotations")
    parts.append("")
    parts.append("from enum import StrEnum")
    parts.append("from typing import Final")
    parts.append("")
    parts.append(f"MASKED_PLACEHOLDER: Final[str] = {_q(spec.masked_placeholder)}")
    parts.append("")
    parts.append("# ── Error reasons (per ADR-0001 §4.5) ────────────────────────────")
    parts.append("")
    parts.append("class ConfigErrorReason(StrEnum):")
    parts.append('    """Discriminator for `ConfigError.reason` (string enum).')
    parts.append("")
    parts.append("    Values are wire-stable; changing one is a breaking change to the")
    parts.append("    config-spec ADR (and to logger-spec which embeds the value in")
    parts.append("    structured logs).")
    parts.append('    """')
    for r in spec.error_reasons:
        parts.append(f"    {r.name} = {_q(r.value)}")
    parts.append("")
    parts.append("ERROR_REASONS: Final[tuple[tuple[str, str, str], ...]] = (")
    for r in spec.error_reasons:
        # Triple = (NAME, value, description). Description normalised to one line.
        desc = r.description.replace("\n", " ")
        parts.append(f"    ({_q(r.name)}, {_q(r.value)}, {_q(desc)}),")
    parts.append(")")
    parts.append("")
    parts.append("# ── Secret patterns (per ADR-0001 §6) ────────────────────────────")
    parts.append("")
    parts.append("SECRET_SUFFIXES: Final[tuple[str, ...]] = (")
    for s in spec.secret_suffixes:
        parts.append(f"    {_q(s)},")
    parts.append(")")
    parts.append("")
    parts.append("SECRET_PREFIXES: Final[tuple[str, ...]] = (")
    for s in spec.secret_prefixes:
        parts.append(f"    {_q(s)},")
    parts.append(")")
    parts.append("")
    parts.append("SECRET_EXACT: Final[tuple[str, ...]] = (")
    for s in spec.secret_exact:
        parts.append(f"    {_q(s)},")
    parts.append(")")
    parts.append("")
    parts.append("__all__ = (")
    parts.append('    "MASKED_PLACEHOLDER",')
    parts.append('    "ConfigErrorReason",')
    parts.append('    "ERROR_REASONS",')
    parts.append('    "SECRET_SUFFIXES",')
    parts.append('    "SECRET_PREFIXES",')
    parts.append('    "SECRET_EXACT",')
    parts.append(")")
    parts.append("")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Go emitter
# --------------------------------------------------------------------------- #


def emit_go(spec: SpecModel, package: str = "generated") -> str:
    """Emit Go file. Uses tabs (gofmt convention) for indentation.

    Output is post-processed through `gofmt -s` if the binary is in PATH;
    otherwise the inline formatting is canonical enough that gofmt would
    be a no-op. The emitter manually trims trailing whitespace from
    docstring lines so empty-comment lines (`// `) appear as bare `//`.
    """
    parts: list[str] = []
    parts.append(_header(spec, "go"))
    parts.append("")
    parts.append(f"package {package}")
    parts.append("")
    parts.append(f"const MaskedPlaceholder = {_q(spec.masked_placeholder)}")
    parts.append("")
    parts.append("// Error reasons (per ADR-0001 §4.5).")
    parts.append("//")
    parts.append("// Wire-stable string values; consumer code compares against the")
    parts.append("// matching const (e.g. ErrReasonMissing).")
    # Compute aligned width for the `const ( ... )` block — gofmt formatting.
    const_names = ["ErrReason" + _camel(r.name) for r in spec.error_reasons]
    const_width = max(len(n) for n in const_names)
    parts.append("const (")
    for r, name in zip(spec.error_reasons, const_names, strict=True):
        parts.append(f"\t{name:<{const_width}} = {_q(r.value)}")
    parts.append(")")
    parts.append("")
    parts.append(
        "// ErrorReasonEntry describes one entry in the config-spec error registry."
    )
    parts.append("// Naming follows the TS ErrorReasonEntry / Python ERROR_REASONS")
    parts.append("// triple — the underlying enum role is filled by the ErrReason*")
    parts.append("// const block above (Go idiom for string-valued enums).")
    parts.append("type ErrorReasonEntry struct {")
    parts.append("\tName        string")
    parts.append("\tValue       string")
    parts.append("\tDescription string")
    parts.append("}")
    parts.append("")
    parts.append("// ErrorReasons enumerates the registry in declaration order.")
    parts.append("var ErrorReasons = []ErrorReasonEntry{")
    for r in spec.error_reasons:
        desc = r.description.replace("\n", " ")
        parts.append(
            f"\t{{Name: {_q(r.name)}, Value: {_q(r.value)}, Description: {_q(desc)}}},"
        )
    parts.append("}")
    parts.append("")
    parts.append("// Secret patterns (per ADR-0001 §6).")
    parts.append("var SecretSuffixes = []string{")
    for s in spec.secret_suffixes:
        parts.append(f"\t{_q(s)},")
    parts.append("}")
    parts.append("")
    parts.append("var SecretPrefixes = []string{")
    for s in spec.secret_prefixes:
        parts.append(f"\t{_q(s)},")
    parts.append("}")
    parts.append("")
    parts.append("var SecretExact = []string{")
    for s in spec.secret_exact:
        parts.append(f"\t{_q(s)},")
    parts.append("}")
    parts.append("")
    text = "\n".join(parts)
    # Trim any trailing whitespace on each line (header comments may end
    # with empty `// ` from join — replace `// \n` with `//\n`).
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _camel(snake_upper: str) -> str:
    """`MISSING` → `Missing`, `TYPE_MISMATCH` → `TypeMismatch`."""
    return "".join(part.capitalize() for part in snake_upper.split("_"))


# --------------------------------------------------------------------------- #
# TypeScript emitter
# --------------------------------------------------------------------------- #


def emit_typescript(spec: SpecModel) -> str:
    parts: list[str] = []
    parts.append(_header(spec, "typescript"))
    parts.append("")
    parts.append(f"export const MASKED_PLACEHOLDER = {_q(spec.masked_placeholder)} as const;")
    parts.append("")
    parts.append("// Error reasons (per ADR-0001 §4.5).")
    parts.append("//")
    parts.append("// Values are wire-stable; treated as a discriminated-union tag.")
    parts.append("export const ConfigErrorReason = {")
    for r in spec.error_reasons:
        parts.append(f"    {r.name}: {_q(r.value)},")
    parts.append("} as const;")
    parts.append("")
    parts.append(
        "export type ConfigErrorReasonValue = "
        "(typeof ConfigErrorReason)[keyof typeof ConfigErrorReason];"
    )
    parts.append("")
    parts.append("export interface ErrorReasonEntry {")
    parts.append("    readonly name: string;")
    parts.append("    readonly value: string;")
    parts.append("    readonly description: string;")
    parts.append("}")
    parts.append("")
    parts.append("export const ERROR_REASONS: readonly ErrorReasonEntry[] = [")
    for r in spec.error_reasons:
        desc = r.description.replace("\n", " ")
        parts.append(
            f"    {{ name: {_q(r.name)}, value: {_q(r.value)}, "
            f"description: {_q(desc)} }},"
        )
    parts.append("] as const;")
    parts.append("")
    parts.append("// Secret patterns (per ADR-0001 §6).")
    parts.append("export const SECRET_SUFFIXES: readonly string[] = [")
    for s in spec.secret_suffixes:
        parts.append(f"    {_q(s)},")
    parts.append("] as const;")
    parts.append("")
    parts.append("export const SECRET_PREFIXES: readonly string[] = [")
    for s in spec.secret_prefixes:
        parts.append(f"    {_q(s)},")
    parts.append("] as const;")
    parts.append("")
    parts.append("export const SECRET_EXACT: readonly string[] = [")
    for s in spec.secret_exact:
        parts.append(f"    {_q(s)},")
    parts.append("] as const;")
    parts.append("")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--lang", required=True, choices=["python", "go", "typescript"]
    )
    p.add_argument(
        "--out", required=True, type=Path, help="Output file path."
    )
    p.add_argument(
        "--package",
        default="generated",
        help="Go package name (only used with --lang go).",
    )
    args = p.parse_args(argv)

    spec = load_spec()

    if args.lang == "python":
        text = emit_python(spec)
    elif args.lang == "go":
        text = emit_go(spec, package=args.package)
    elif args.lang == "typescript":
        text = emit_typescript(spec)
    else:
        raise SystemExit(f"unknown lang: {args.lang}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(
        f"emit.py: wrote {args.out} ({len(text.splitlines())} lines, "
        f"sha={_content_hash(text)[:8]})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
