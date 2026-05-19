/**
 * Cross-binding round-trip CI: TypeScript wrapper.
 *
 * Invocation (with `tsx`):
 *
 *     npx tsx scripts/canonical_typescript.ts <fixture.yaml> [<layer.yaml> ...]
 *
 * Reads one or more YAML / JSON fixtures, deep-merges them in order
 * through the binding's `deepMergeAll` (priority: last wins —
 * ADR-0001 §3), and emits canonical JSON of the merged tree to stdout.
 *
 * Multi-file invocation exercises layered semantics — the only path
 * that covers `Config.loadFrom([YamlFileSource, ...])` deep-merge
 * across maps + atomic list-replace. Single-file invocation is a
 * strict subset (deepMergeAll of a one-element list is identity), so
 * the wrapper handles both cases uniformly.
 *
 * The binding exposes `canonicalize` and `deepMergeAll` from the
 * package entry point since v0.1.
 *
 * Resolution: the script imports the binding via the package name
 * `@dagstack/config`, which the workflow links via `npm link` from a
 * sibling `dagstack/config-typescript` checkout before invocation.
 */

import { readFileSync } from "node:fs";
import { argv, exit, stdout } from "node:process";

import { parse as parseYaml } from "yaml";
import {
    canonicalize,
    deepMergeAll,
    type ConfigValue,
} from "@dagstack/config";

function main(): number {
    if (argv.length < 3) {
        process.stderr.write(
            "usage: canonical_typescript.ts <fixture.yaml> [<layer.yaml> ...]\n",
        );
        return 2;
    }
    const fixturePaths = argv.slice(2);
    const trees: ConfigValue[] = fixturePaths.map((p) => {
        const text = readFileSync(p, "utf8");
        const loaded = parseYaml(text);
        // YAML-empty file → null — deepMergeAll expects a mapping.
        return (loaded ?? {}) as ConfigValue;
    });
    const merged = deepMergeAll(trees);
    stdout.write(canonicalize(merged));
    return 0;
}

exit(main());
