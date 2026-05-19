# ADR-0003: Push-based secret rotation and cloud secret-manager adapters (candidate)

- **Status:** proposed (candidate)
- **Revision:** 0.1 (2026-05-15) — initial placeholder. Future
  revisions will be added as the candidate accumulates concrete
  decisions from operator feedback.
- **Date:** 2026-05-15
- **Architect review:** pending — review opens when Phase 2
  operational data (see §"Pre-conditions for acceptance") is
  available.
- **Supersedes:** —
- **Amends:** ADR-0002 §11 (Subscription extension preview); under
  Q3 option (A), this ADR will also extend ADR-0001 §7.2
  (`ChangeEvent` shape — add `event_type` discriminator field).
- **Related:** ADR-0001 §7 (subscription model), ADR-0002 (Phase 2
  pull-based secret resolution), HashiCorp Vault Agent caching,
  AWS Secrets Manager rotation via EventBridge, GCP Pub/Sub on
  Secret Manager rotation, Kubernetes External Secrets Operator
  (push reconciliation).

## Context

ADR-0002 landed Phase 2 of the secrets surface: a pull-based
`SecretSource` contract, `EnvSecretSource` and `VaultSource` adapters,
lazy `SecretRef` resolution, and `Config.refresh_secrets()` for
operator-driven rotation. Token-renewal background tasks and
push-based change notification were explicitly deferred — Phase 2 ships
with long-TTL tokens, AppRole / Kubernetes auth, and operator-issued
refresh.

The deferral was made under two arguments:

1. **No production data yet.** Until at least one operator runs the
   Phase 2 surface against rotation cadences observed in real
   deployments, every choice (poll interval, notification fan-out,
   retry / back-off, multi-region replicas, cache invalidation under
   contention) would be designed against guesswork.

2. **Subscription model still settling.** ADR-0001 §7 specifies a
   subscription contract but Phase 1 bindings return an inactive
   handle. Implementing real subscriptions for *config* changes lands
   before secret subscriptions to reuse the plumbing.

This ADR is a **tracking candidate** for the Phase 3 design discussion.
It contains no normative decisions; those land as numbered revisions
(`v0.2`, `v0.3`, …) as operator feedback resolves the open questions
below. When every open question has an accepted
answer, the ADR moves from `proposed (candidate)` to `accepted`.

The "candidate" status borrows the convention used by IETF
Internet-Drafts: issues raised against the document are tracked here,
addressed through revisions, and the ADR is promoted only when the
design is stable enough to bind implementations.

## Scope

Phase 3 covers four loosely coupled areas. Each can land
independently (separate PRs against this ADR), but the design choices
in one affect the others enough to keep them in a single document.

### A. Push-based rotation channel

ADR-0002 §11 sketched an `onSecretChange(scheme, path, callback)`
extension to the existing `Subscription` handle from ADR-0001 §7.2.

**Architectural risk to keep in mind.** Each backend's native push
mechanism has a different shape — Vault lease callbacks, K8s
informer watch-bookmarks with resource-version cursors, AWS-SM
EventBridge JSON events, GCP Pub/Sub messages. Designing the §A
channel around any single backend's idiom is likely to fit poorly on
the others. The pilot choice (§"Implementation roadmap" §B.1)
therefore **defines the shape** of the §A contract — pick a pilot
whose semantics are closest to the lowest common denominator, or
accept that later adapters will wrap their native channels into a
shape that loses some upstream richness. The lowest-common-denominator
vs adapter-shaped trade-off is the subject of Q7 / Q8 below.

The Phase 3 question set:

- What does the `SecretChangeEvent` payload look like (path, scheme,
  source_id, new value vs notification-only)?
- Which backends actually push? Vault Agent caching + lease watcher,
  AWS-SM EventBridge events, GCP Secret Manager Pub/Sub
  notifications, K8s informer for `Secret` resources. Each has a
  different latency profile (Vault: seconds; AWS-SM: 10–60 s through
  EventBridge; Kubernetes: watch-based, sub-second).
- Fall-back to polling when push is unavailable (offline operator,
  air-gapped deploy, push-channel outage).
- Coalescing semantics — rapid back-to-back rotations within a single
  TTL window should produce one operator-visible event, not N.

### B. Cloud secret-manager adapters

`_meta/secret_schemes.yaml` reserves three Phase 3 schemes:

- `awssm` → `AwsSecretsManagerSource` — JSON-typed secrets,
  versioning (`AWSCURRENT` / `AWSPENDING` / `AWSPREVIOUS`), rotation
  lambdas, IAM-based auth, region routing.
- `gcpsm` → `GcpSecretManagerSource` — versioned secrets, per-key
  IAM, multi-region replicas, resource-name addressing
  (`projects/<p>/secrets/<n>/versions/<v>`).
- `k8ssecret` → `K8sSecretSource` — in-cluster `Secret` objects,
  namespace-scoped RBAC, watch-based change notification.

Each adapter mirrors `VaultSource` in shape (lazy `resolve`, JSON
envelope handling, `#field` projection, optional `?version=` query)
but the auth model and the rotation-notification channel differ. The
adapter for Kubernetes in particular fuses with §A — its native event
source is the K8s informer, so push delivery and adapter implementation
are the same code path.

### C. Token-renewal background hooks and observability

Phase 2 deferred renewal entirely (see per-binding ADRs:
`config-python/adr/0001-vault-source.md` §4,
`config-typescript/adr/0001-vault-source.md` §4,
`config-go/adr/0001-vault-source.md` §4). Phase 3 introduces:

- An optional `start()` / `close()` lifecycle on `SecretSource` for
  adapters that need background goroutines / asyncio tasks /
  `setInterval` timers.
- A renewal cadence the adapter computes from upstream
  metadata (Vault token TTL, AWS-SM rotation schedule).
- Cancellation semantics — `Config.close()` MUST stop renewal tasks
  cleanly; partial-shutdown leaks must surface as observable errors.

**Observability surface.** Push rotation is a real asynchronous
subsystem (background tasks, retry, back-off, fan-out across N
callbacks). Operators MUST be able to diagnose "why does my config
think Vault is silent?" — Phase 2 already emits `secret_resolve_*`
events through logger-spec; Phase 3 doubles this surface. The
candidate proposes (subject to Q8):

- Metrics / events for `renewal_attempt`, `renewal_failed`,
  `push_received`, `push_dropped` (coalesced), `polling_fallback_active`.
- Correlation through `change_id` / `tx_id` so an operator can
  pivot from a push event to its downstream resolves.
- Naming aligned with logger-spec's `secret_resolve_*` family so a
  single dashboard covers both phases.

### D. Composite and dynamic-secret patterns

Two derived patterns surface once §A-C exist:

- **`CompositeSecretSource`** — fan-out to multiple backends under
  one scheme (e.g., `vault-primary` + `vault-dr` with automatic
  fail-over). Composition lives in user code today; the spec may
  promote it to a normative pattern if multi-backend deployments
  become common.
- **Dynamic secrets** — Vault's `database/creds/<role>` issues a
  leased credential pair. Resolution is a request-response, not a
  cached lookup, and the lease must be renewed in the background.
  This is the canonical use case for §C; the secret is unusable
  without §C plumbing.

## Open design questions

These are the questions whose answers will become normative decisions
in `v0.2`+ of this ADR.

### Q1 — `SecretChangeEvent` shape (notification vs full value)

**Question.** Does the push channel deliver the new value, or only a
notification ("path X rotated; re-read it on next access")?

**Trade-offs.** Full-value delivery cuts a round-trip per rotation but
requires the channel to be at least as protected as the secret itself
(end-to-end encryption, audited delivery). Notification-only keeps
the channel trust boundary low and pairs naturally with the existing
lazy-resolution model — the next `get*` triggers a fresh `resolve()`.

**Provisional answer.** Notification-only is the safer default; full
value delivery is a per-adapter opt-in for channels with explicit
trust guarantees (mTLS Vault Agent socket, signed AWS EventBridge
events).

### Q2 — Polling fallback policy

**Question.** When push is unavailable, do bindings poll, and at what
cadence?

**Trade-offs.** A 60-second poll guarantees rotation visibility
within a minute but adds N requests per minute per secret to every
running instance. A 5-minute poll is gentler but lets a rotated
secret stay live for up to 5 minutes after rotation. Vault leases
typically expire within minutes — a 5-minute poll may produce
authentication failures before the poll notices.

**Provisional answer.** Adaptive cadence driven by upstream TTL —
poll at `min(ttl/2, 5min)` with ±10 % jitter to spread fleet load.
Hard cap at `5min` to bound worst case. Operators with sub-minute
rotation cadences (PCI-DSS, dynamic database creds) override per
source through an adapter constructor argument.

### Q3 — Subscription contract reuse

**Question.** Should secret change events ride the existing
`onChange` / `onSectionChange` channel from ADR-0001 §7.2, or
warrant a separate `onSecretChange` API (the preview from ADR-0002
§11)?

**Trade-offs.** Reusing one channel keeps the operator's mental
model simple ("one place to register interest in config changes") at
the cost of mixing two latency profiles (config-file inotify: ms;
secret push: 10 s+). A separate channel preserves the latency
contract per channel but doubles the surface area to learn.

Three concrete options:

- **(A) Reuse** — `onChange` / `onSectionChange` carry both, with
  an `event_type` discriminator on `ChangeEvent`.
- **(B) Separate** — `onSecretChange` becomes its own normative
  surface; `onChange` is config-file-only.
- **(C) Sugar** — `onSecretChange` ships as a thin wrapper over
  (A) so operators get either ergonomic, but the spec defines only
  one channel.

**Provisional answer.** Option (A) — one channel with an
`event_type` discriminator on `ChangeEvent`
(`event_type: "config" | "secret"`). Snake-case mirrors the rest of
`ChangeEvent` (`change_id`, `source_id`, `old_value`); the field
name avoids collision with the ADR-0001 `kind` source-classification
field. Operators that filter already need code; the discriminator
costs them nothing extra.

### Q4 — Atomic visibility across multiple rotated paths

**Question.** When two co-rotated secrets land on the operator at
different times (eventual-consistency window in the backend), should
the loader wait for both before firing a change event?

**Trade-offs.** Atomic visibility (wait for the second value before
firing) gives the operator a consistent view but introduces an
unbounded wait time when the second backend is slow. Per-path
firing surfaces each rotation immediately; the operator handles
their own staging.

**Provisional answer.** Per-path firing with a `tx_id` correlation
field, so an operator that needs atomicity can join events on
`tx_id`. Atomic delivery is a Phase 4 question if anyone asks.

### Q5 — Renewal-on-failure back-off

**Question.** When a renewal call fails (transient network, expired
auth, backend down), what is the back-off schedule before the
adapter surfaces `secret_backend_unavailable`?

**Trade-offs.** Aggressive retry burns CPU and may overwhelm a
recovering backend (effectively a self-inflicted DoS). Conservative
retry leaves consumers with a stale token longer.

**Provisional answer.** Exponential back-off (1 s → 2 s → 4 s → 8 s,
cap at 30 s, jittered) for transient errors. Permanent errors
(403 Forbidden, sustained 404 after initial successful resolves)
bypass back-off and surface immediately. A 404 on the very first
resolve is treated as transient — Vault returns 404 for empty paths
that may exist later.

### Q6 — Composite source as a spec primitive vs user code

**Question.** Is `CompositeSecretSource` part of the spec, or a
documented pattern in user-land?

**Trade-offs.** Promoting it to the spec adds a normative API that
every binding must ship; the implementation is small enough that
the maintenance cost is modest. Keeping it in user-land respects
the principle that the spec ships *minimum normative surface*.

**Provisional answer.** Document the pattern in operator-facing
docs; let it migrate to the spec if multi-backend deployments become
common enough to justify normative status.

### Q7 — Transport-security guarantees for push channels

**Question.** What MUST a binding verify before it trusts a push
event?

**Trade-offs.** The four push channels have different native
security primitives:

- **Vault Agent unix socket** — local file with operator-controlled
  filesystem permissions, no in-band signature.
- **AWS-SM EventBridge** — JSON events through SNS; signature
  verification via the SNS topic's public key.
- **GCP Pub/Sub** — IAM-authenticated subscription; no per-message
  signature.
- **Kubernetes informer** — TLS to the API server with
  ServiceAccount bearer token; RBAC governs visibility.

A push channel that delivers full values (Q1) inherits the secret's
trust boundary; a notification-only channel relaxes the requirement
but the binding still trusts the channel to truthfully report
"path X changed."

**Provisional answer.** Each adapter documents its native trust
model in the per-binding ADR. The cross-binding contract is
minimal: a push event MUST come through a channel the adapter
authenticated at startup (mTLS, IAM, ServiceAccount). Bindings
MUST drop events that fail signature verification (where available)
and MUST log `push_dropped` with the reason. Operators that need
end-to-end signatures for full-value delivery configure their
secret manager to sign rotations explicitly; bindings expose the
public key through adapter constructor options.

### Q8 — Observability and metrics surface for Phase 3

**Question.** Which Phase 3 events MUST a binding emit, and through
which channel (logger-spec structured events vs a separate metrics
contract)?

**Trade-offs.** Emitting everything through logger-spec keeps the
operator's mental model (`grep secret_resolve` already works).
Adding a parallel metrics contract (Prometheus-compatible counters,
OTel histograms) gives proper SLO surface but doubles the
maintenance burden.

**Provisional answer.** logger-spec structured events for the
v0.2-v0.3 candidate revisions; if operator feedback shows real
SLO needs, layer Prometheus/OTel metrics on top in v0.4. The
event names from §C ("Observability surface") above are normative
once Q8 accepts.

### Q9 — Dynamic secrets vs static secrets in the same `SecretRef`

**Question.** Can `${secret:vault:database/creds/myrole}` co-exist
in a tree with `${secret:vault:secret/data/prod/api}`? They have
different lifecycle (dynamic: per-resolve, leased; static: cached,
TTL-bounded).

**Trade-offs.** Allowing both gives operators a single mental model
but introduces an invisible per-path semantic split — dynamic
secrets cannot be cached safely. Banning dynamic in a `SecretRef`
("only `Config.fetchDynamicSecret` returns dynamics") avoids the
trap but adds a parallel API.

**Provisional answer.** Allow both with a query flag —
`${secret:vault:database/creds/myrole?lease=true}` opts in to the
per-resolve lifecycle; the default is cached-static semantics. The
flag is an explicit opt-in to the contract change.

## Pre-conditions for acceptance

This ADR moves from `proposed (candidate)` to `accepted` when all of
the following are true:

1. **Phase 2 operational data.** At least one operator runs the
   Phase 2 surface (pull-based `VaultSource` + `Config.refresh_secrets()`)
   against production traffic for at least one quarter, with
   rotation cadence and observed incident notes captured in a
   public retrospective.

2. **Subscription plumbing live.** ADR-0001 §7 subscriptions deliver
   real config-file events in all three bindings (Python /
   TypeScript / Go). Until then, secret subscriptions have no
   plumbing to ride on.

3. **At least one cloud-manager pilot.** One of `awssm`, `gcpsm`,
   `k8ssecret` ships as a working prototype in any single binding
   (does not have to be feature-complete). The prototype reveals
   the auth-model differences (IAM vs OIDC vs in-cluster SA) that
   the §C lifecycle hooks must accommodate.

4. **Operator feedback.** At least one open issue / discussion in
   the spec repository raises a concrete counter-proposal to any
   provisional answer in Q1-Q9 — the counter-proposal must be
   specific enough to either accept or refute through a numbered
   revision. Candidate revisions absorb the feedback before
   promotion; the revision history names which issue resolved
   which question.

## Out of scope

- **Encryption-at-rest of cached secrets.** Bindings cache resolved
  values in process memory; encrypting that cache is an OS / runtime
  concern (TPM, secure enclaves, locked memory), not a config-spec
  contract.
- **Per-request audit log.** Audit lives in the secret manager
  (Vault audit device, AWS CloudTrail, GCP Audit Logs); the binding
  surfaces only its own resolve calls in structured logs. Cross-
  cutting audit is a logger-spec concern.
- **Secret zeroization on close.** The binding cannot guarantee
  memory-safe zeroization in any of the three target languages
  (Go and TypeScript GC, Python refcounting). Operators that need
  zeroization should fetch secrets through a sidecar that does
  guarantee it (Vault Agent unix socket, AWS-SM at-rest filesystem).
- **Phase 4 features.** Multi-tenancy of `SecretSource` instances
  per `Config`, signed-secret verification, hardware-attested
  secrets — all deferred to a future ADR when the use case is
  concrete.

## Implementation roadmap (provisional)

When this ADR reaches `accepted`, implementation will likely split
into PR groups (order subject to operator feedback):

1. **§A.1** — `SecretChangeEvent` normative shape + `_meta/types.yaml`
   row + emitter generation.
2. **§A.2** — subscription-channel reuse decision from Q3 (either
   extend `onChange` / `onSectionChange` from ADR-0001 §7.2 with an
   `event_type` discriminator, or surface `onSecretChange` from
   ADR-0002 §11 as its own normative API). One binding (probably
   Python pilot) ships the working subscription path; conformance
   fixtures land alongside.
3. **§B.1** — `K8sSecretSource` first (informer-driven; serves as
   the §A.2 push-channel implementation).
4. **§B.2** — `AwsSecretsManagerSource` second (EventBridge gives
   real push semantics).
5. **§B.3** — `GcpSecretManagerSource` third (Pub/Sub; similar
   pattern).
6. **§C** — token-renewal hooks land alongside the second binding
   adoption of §A.2; lifecycle (`start` / `close`) lands in
   `SecretSource` contract revision.
7. **§D** — Composite + dynamic patterns documented as the
   patterns emerge from §B.1-3.

## Revision history (planned)

- `v0.1` (this revision) — candidate placeholder; collects open
  questions and pre-conditions. No normative content.
- `v0.2` (future) — provisional answers to Q1-Q9 promoted to
  decisions; section §A.1 normative.
- `v0.3` (future) — push channel + first cloud adapter normative;
  conformance fixtures land.
- `v1.0` (future) — full Phase 3 surface accepted; ADR promoted to
  `accepted`.

## References

- ADR-0001 — Phase 1 base spec (config sources, env interpolation,
  subscription contract §7).
- ADR-0002 — Phase 2 pull-based secrets (this ADR's foundation).
- Per-binding Vault ADRs — `config-python/adr/0001-vault-source.md`,
  `config-typescript/adr/0001-vault-source.md`,
  `config-go/adr/0001-vault-source.md` (§4 in each defers renewal to
  Phase 3).
- HashiCorp Vault — Agent caching + lease watcher patterns.
- AWS Secrets Manager — rotation notifications via EventBridge.
- GCP Secret Manager — Pub/Sub on rotation events.
- Kubernetes External Secrets Operator — push reconciliation via
  informer.
