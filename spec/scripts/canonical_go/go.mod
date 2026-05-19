module dagstack/config-spec/scripts/canonical_go

go 1.22

require (
	go.dagstack.dev/config v0.0.0
	gopkg.in/yaml.v3 v3.0.1
)

// The local sibling checkout of dagstack/config-go provides
// `go.dagstack.dev/config`. This `replace` keeps the wrapper buildable
// from a local clone (`../dagstack-config-go`) and from CI (which
// clones the repo to the same relative position before invoking
// `go run ./scripts/canonical_go`). When the binding is published to
// the Go proxy at `go.dagstack.dev/config`, this replace can be
// dropped or pinned to a tag.
replace go.dagstack.dev/config => ../../../dagstack-config-go
