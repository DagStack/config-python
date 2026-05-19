# dagstack-config

Python binding for [dagstack/config-spec](https://github.com/dagstack/config-spec) — YAML configuration with env interpolation, deep-merge layering, Pydantic-based typed sections, secret references with pluggable backends.

**Status:** Phase 1 (`0.4.x`) shipped on PyPI. Phase 2 secrets (`0.5.x`) shipped with HashiCorp Vault as the pilot adapter under the `[vault]` extra.

## Secrets (Phase 2 — `0.5.0+`)

Per [ADR-0002](https://github.com/dagstack/config-spec/blob/main/adr/0002-secret-references-and-sources.md), Phase 2 adds the `${secret:<scheme>:<path>}` interpolation token alongside Phase 1's `${VAR}`. Pluggable `SecretSource` adapters resolve the references at load (eager) or first read (lazy, default).

The `env` scheme is auto-registered and behaves identically to Phase 1's `${VAR}`:

```yaml
# app-config.yaml
llm:
  api_key: ${secret:env:OPENAI_API_KEY}        # ≡ ${OPENAI_API_KEY}
  fallback: ${secret:env:OPENAI_API_KEY:-sk-dev-placeholder}
```

The pilot HashiCorp Vault adapter ships in the `[vault]` extra:

```bash
pip install 'dagstack-config[vault]'
```

```python
import os

from dagstack.config import Config, YamlFileSource
from dagstack.config.vault import VaultSource, TokenAuth

cfg = Config.load_from([
    YamlFileSource("app-config.yaml"),
    VaultSource(
        addr="https://vault.example.com",
        auth=TokenAuth(token=os.environ["VAULT_TOKEN"]),
        namespace="dagstack/prod",
    ),
])
api_key = cfg.get_string("llm.api_key")
# ${secret:vault:secret/dagstack/prod/openai#api_key}
```

`?version=N` selects a specific KV v2 version; `#field` plucks a sub-key from a multi-key secret. AppRole and Kubernetes ServiceAccount auth are supported alongside `TokenAuth` — see `adr/0001-vault-source.md` for details.

## Roadmap

- **Phase 1 (`0.4.x`)** — base spec MVP: file sources, env interpolation, deep-merge layering, Pydantic typed sections, canonical JSON.
- **Phase 2 (`0.5.x`)** — secret references + pluggable `SecretSource` adapters (per ADR-0002). VaultSource pilot under `[vault]`.
- **Phase 3+** — push-based rotation events, AWS / GCP / K8s secret-manager adapters, watch + push-reload of file sources.

## Thread-safety and usage contract

`Config` is designed for share-nothing reads after construction. The contract below is binding for the `0.x` line; revisions for the watch / hot-reload work in Phase 3+ will be tracked in CHANGELOG and the `dagstack/config-spec` ADRs.

### Reads are concurrent-safe

`Config.get(...)` / `get_string` / `get_int` / `get_number` / `get_bool` / `get_list` / `get_section(...)` and `has(...)` / `source_ids()` are pure reads from the merged tree built during `Config.load*`. Once `Config` is constructed the tree is treated as immutable: no public method mutates it.

The reference implementation does not take any locks on the read path. Concurrent reads from any number of OS threads are safe under CPython's GIL — dict lookups, list indexing and attribute reads are atomic at the bytecode level. `source_ids()` returns a fresh `list` copy on every call, so callers cannot mutate internal state.

> **Note (free-threaded CPython 3.13+).** The PEP 703 build mode (no GIL) is not part of the conformance matrix yet. Reads of nested dicts/lists may require explicit synchronization under future free-threaded interpreters; this will be revisited before declaring 3.13t support.

### `reload()` semantics

In Phase 1 / Phase 2 `Config.reload()` is a no-op for every built-in source — none of `YamlFileSource`, `JsonFileSource`, `InMemorySource`, `EnvSecretSource`, `VaultSource` emit push events. Calling it from any thread is harmless.

Phase 3+ will introduce push-capable sources (etcd, Consul, HTTP) and rotation events from secret backends. The contract for `reload()` then becomes: a single writer atomically swaps the internal tree reference; concurrent readers in flight observe either the previous or the next tree, never a torn / partially merged state. Callers that need a consistent snapshot across multiple `get(...)` calls should bind a local reference to the section once (`section = config.get_section("db", DbConfig)`) and reuse it.

### Subscriptions are inactive in `0.5.x`

`on_change(...)` and `on_section_change(...)` register a `Subscription` with `active=False` and emit a `subscription_without_watch` warning on the `dagstack.config.internal` logger. The callback is never invoked. The methods themselves are safe to call from any thread.

When push-capable subscriptions ship in Phase 3+, registration / unregistration will be guarded by an internal lock. The exact dispatch model (background thread, executor, async task) is not yet normatively fixed by `dagstack/config-spec` — application code MUST treat callback bodies as non-blocking and free of long-running I/O regardless of the chosen model, and MUST NOT assume ordering guarantees between independent subscribers.

### asyncio

The `Config` API is synchronous and in-memory. Calls take well below a millisecond and do not need to be wrapped in `asyncio.to_thread` — invoking them directly from a coroutine does not block the event loop.

For hot paths it is still recommended to materialize a typed section once during startup and reuse the resulting Pydantic model:

```python
db_config = config.get_section("database", DatabaseConfig)
# pass db_config around; do not re-walk the tree on every request
```

### Instances and ownership

`Config.load(...)` / `load_paths(...)` / `load_from(...)` return a fresh instance on every call. The binding does not impose a singleton: storing the instance as a module-global, in a DI container or per-request is the application's choice.

## Spec

The spec submodule lives in `spec/` (pointing to `dagstack/config-spec`). Normative decisions are recorded in `spec/adr/0001-yaml-configuration.md`.

## Local development

```bash
git clone --recurse-submodules git@github.com:dagstack/config-python.git
cd config-python
uv sync --group dev

make test           # pytest
make lint           # ruff check + format --check
make typecheck      # mypy --strict
```

## Licensing

Apache-2.0 (see [LICENSE](./LICENSE)).

## Related

- [`dagstack/config-spec`](https://github.com/dagstack/config-spec) — language-agnostic spec.
- [`dagstack/logger-spec`](https://github.com/dagstack/logger-spec) — logger that reads config through this binding.
- [`dagstack/plugin-system-python`](https://github.com/dagstack/plugin-system-python) — reference binding (pattern and Makefile/CI structure).
