// Cross-binding round-trip CI: Go wrapper.
//
// Invocation (with `go run`):
//
//	go run ./scripts/canonical_go <fixture.yaml> [<layer.yaml> ...]
//
// Reads one or more YAML / JSON fixtures, deep-merges them in order
// through the binding's public `LoadFrom` over `DictSource` sources
// (priority: last wins — ADR-0001 §3), and emits canonical JSON of
// the merged tree to stdout.
//
// Multi-file invocation exercises layered semantics — the only path
// that covers `Config.LoadFrom([..., ...])` deep-merge across maps +
// atomic slice-replace. Single-file invocation is a strict subset
// (Snapshot of a one-source merge is identity), so the wrapper
// handles both cases uniformly.
//
// Why DictSource rather than YamlFileSource? `YamlFileSource.Load`
// runs raw-text `${VAR}` interpolation before YAML decode and the
// public API exposes no opt-out. The Python and TypeScript wrappers
// route their YAML through the binding's `deep_merge_all` /
// `deepMergeAll` primitives, which bypass interpolation by design.
// To stay byte-equal across the three bindings we mirror that path
// in Go: yaml-decode the file ourselves, wrap each decoded Tree as a
// `DictSource` (defaults to `Interpolate()==false`), and let
// `LoadFrom` perform the deep-merge. The end-to-end semantics across
// all three wrappers becomes "YAML 1.2 parse → deep-merge → canonical
// JSON" — exactly what `_meta/canonical_json.yaml` fixes.
//
// Resolution: the wrapper module declares a require + replace
// directive pointing at a sibling dagstack/config-go checkout (set up
// by the workflow). The binding exposes `LoadFrom`, `NewDictSource`,
// `Snapshot`, and `CanonicalJSON` since v0.4.
package main

import (
	"context"
	"fmt"
	"os"

	"go.dagstack.dev/config"
	"gopkg.in/yaml.v3"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: canonical_go <fixture.yaml> [<layer.yaml> ...]")
		os.Exit(2)
	}
	sources := make([]config.Source, 0, len(os.Args)-1)
	for _, p := range os.Args[1:] {
		raw, err := os.ReadFile(p)
		if err != nil {
			fmt.Fprintf(os.Stderr, "read %s: %v\n", p, err)
			os.Exit(1)
		}
		var decoded any
		if err := yaml.Unmarshal(raw, &decoded); err != nil {
			fmt.Fprintf(os.Stderr, "parse %s: %v\n", p, err)
			os.Exit(1)
		}
		// YAML-empty file decodes to nil; LoadFrom expects a Tree.
		var tree config.Tree
		if decoded == nil {
			tree = config.Tree{}
		} else {
			m, ok := decoded.(map[string]any)
			if !ok {
				fmt.Fprintf(os.Stderr, "parse %s: top-level must be a mapping\n", p)
				os.Exit(1)
			}
			tree = config.Tree(m)
		}
		sources = append(sources, config.NewDictSource(tree).WithID("file:"+p))
	}
	cfg, err := config.LoadFrom(context.Background(), sources)
	if err != nil {
		fmt.Fprintf(os.Stderr, "load: %v\n", err)
		os.Exit(1)
	}
	out, err := config.CanonicalJSON(cfg.Snapshot())
	if err != nil {
		fmt.Fprintf(os.Stderr, "canonicalize: %v\n", err)
		os.Exit(1)
	}
	if _, err := os.Stdout.Write(out); err != nil {
		fmt.Fprintf(os.Stderr, "write stdout: %v\n", err)
		os.Exit(1)
	}
}
