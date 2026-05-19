"""Phase 2 runtime API: refresh_secrets / snapshot / eager_secrets / TTL.

Per ADR-0002 §3:
- `Config.refresh_secrets()` MUST drop the cache and force re-resolution
  on next access (manual rotation hook).
- `Config.snapshot()` MUST replace every SecretRef with `[MASKED]`
  without resolving the reference (default).
- `Config.snapshot(include_secrets=True)` MAY resolve and apply
  field-name suffix masking (audit-mode opt-in).
- `Config.load_from(..., eager_secrets=True)` MUST resolve every
  SecretRef at load time and surface backend errors immediately.
- The cache MUST honour `expires_at` from `SecretValue` if present.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from dagstack.config import Config, ConfigError, YamlFileSource
from dagstack.config.errors import ConfigErrorReason
from dagstack.config.secrets import ResolveContext, SecretSource, SecretValue
from dagstack.config.secrets_mask import MASKED_PLACEHOLDER

if TYPE_CHECKING:
    from pathlib import Path


class _CountingSource:
    """SecretSource stub that records every resolve() call."""

    def __init__(self, value: str = "v1", expires_at: datetime | None = None) -> None:
        self._value = value
        self._expires_at = expires_at
        self.resolve_calls: list[str] = []

    @property
    def scheme(self) -> str:
        return "ctr"

    @property
    def id(self) -> str:
        return "ctr:test"

    def resolve(self, path: str, ctx: ResolveContext) -> SecretValue:
        del ctx
        self.resolve_calls.append(path)
        return SecretValue(
            value=self._value,
            source_id=self.id,
            expires_at=self._expires_at,
        )

    def close(self) -> None:
        pass


def _yaml(tmp_path: Path, body: str) -> YamlFileSource:
    f = tmp_path / "c.yaml"
    f.write_text(body)
    return YamlFileSource(f)


# ─── refresh_secrets() ──────────────────────────────────────────────────────


def test_refresh_secrets_drops_cache(tmp_path: Path) -> None:
    src = _CountingSource(value="v1")
    cfg = Config.load_from([_yaml(tmp_path, "k: ${secret:ctr:foo}\n"), src])

    assert cfg.get_string("k") == "v1"
    assert cfg.get_string("k") == "v1"  # cached → no second round-trip
    assert len(src.resolve_calls) == 1

    cfg.refresh_secrets()
    assert cfg.get_string("k") == "v1"
    assert len(src.resolve_calls) == 2


def test_refresh_secrets_picks_up_new_value(tmp_path: Path) -> None:
    src = _CountingSource(value="v1")
    cfg = Config.load_from([_yaml(tmp_path, "k: ${secret:ctr:foo}\n"), src])

    assert cfg.get_string("k") == "v1"
    src._value = "v2"  # rotated at the backend
    assert cfg.get_string("k") == "v1"  # cache still serves old
    cfg.refresh_secrets()
    assert cfg.get_string("k") == "v2"


# ─── snapshot() ─────────────────────────────────────────────────────────────


def test_snapshot_default_masks_secret_refs_without_resolving(tmp_path: Path) -> None:
    src = _CountingSource(value="should-not-appear")
    cfg = Config.load_from(
        [_yaml(tmp_path, "api_key: ${secret:ctr:foo}\nplain: hello\n"), src],
    )

    snap = cfg.snapshot()
    assert snap == {"api_key": MASKED_PLACEHOLDER, "plain": "hello"}
    # Critically — no backend round-trip.
    assert src.resolve_calls == []


def test_snapshot_include_secrets_resolves_then_field_masks(tmp_path: Path) -> None:
    src = _CountingSource(value="resolved-secret")
    cfg = Config.load_from(
        [
            _yaml(
                tmp_path,
                "api_key: ${secret:ctr:foo}\nendpoint: ${secret:ctr:bar}\n",
            ),
            src,
        ],
    )

    snap = cfg.snapshot(include_secrets=True)
    # `api_key` matches secret-name pattern → still masked.
    assert snap["api_key"] == MASKED_PLACEHOLDER
    # `endpoint` does not match → resolved value visible.
    assert snap["endpoint"] == "resolved-secret"
    assert len(src.resolve_calls) == 2


def test_snapshot_masks_plain_string_under_secret_name(tmp_path: Path) -> None:
    cfg = Config.load_from([_yaml(tmp_path, "password: hunter2\nuser: alice\n")])
    snap = cfg.snapshot()
    assert snap == {"password": MASKED_PLACEHOLDER, "user": "alice"}


def test_snapshot_returns_independent_copy(tmp_path: Path) -> None:
    cfg = Config.load_from([_yaml(tmp_path, "x: {y: 1}\n")])
    snap = cfg.snapshot()
    snap["x"]["y"] = 999
    assert cfg.get_int("x.y") == 1


# ─── eager_secrets=True ─────────────────────────────────────────────────────


def test_eager_secrets_resolves_at_load(tmp_path: Path) -> None:
    src = _CountingSource(value="v1")
    cfg = Config.load_from(
        [_yaml(tmp_path, "k: ${secret:ctr:foo}\n"), src],
        eager_secrets=True,
    )
    assert src.resolve_calls == ["foo"]
    # Subsequent get* hits the cache.
    assert cfg.get_string("k") == "v1"
    assert src.resolve_calls == ["foo"]


def test_eager_secrets_surfaces_backend_error_at_load(tmp_path: Path) -> None:
    class _FailingSource:
        @property
        def scheme(self) -> str:
            return "fail"

        @property
        def id(self) -> str:
            return "fail:test"

        def resolve(self, path: str, ctx: ResolveContext) -> SecretValue:
            del path, ctx
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_BACKEND_UNAVAILABLE,
                details="backend down",
            )

        def close(self) -> None:
            pass

    with pytest.raises(ConfigError) as exc_info:
        Config.load_from(
            [_yaml(tmp_path, "k: ${secret:fail:foo}\n"), _FailingSource()],
            eager_secrets=True,
        )
    assert exc_info.value.reason is ConfigErrorReason.SECRET_BACKEND_UNAVAILABLE


def test_eager_secrets_default_is_lazy(tmp_path: Path) -> None:
    src = _CountingSource(value="v1")
    Config.load_from([_yaml(tmp_path, "k: ${secret:ctr:foo}\n"), src])
    assert src.resolve_calls == []


# ─── expires_at honoured ────────────────────────────────────────────────────


def test_cache_honours_expires_at(tmp_path: Path) -> None:
    src = _CountingSource(
        value="v1",
        expires_at=datetime.now(tz=UTC) - timedelta(seconds=1),  # already expired
    )
    cfg = Config.load_from([_yaml(tmp_path, "k: ${secret:ctr:foo}\n"), src])

    assert cfg.get_string("k") == "v1"
    assert cfg.get_string("k") == "v1"
    # Expired immediately → every read is a fresh round-trip.
    assert len(src.resolve_calls) == 2


def test_cache_does_not_honour_future_expires_at(tmp_path: Path) -> None:
    src = _CountingSource(
        value="v1",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),  # well in future
    )
    cfg = Config.load_from([_yaml(tmp_path, "k: ${secret:ctr:foo}\n"), src])

    cfg.get_string("k")
    cfg.get_string("k")
    cfg.get_string("k")
    assert len(src.resolve_calls) == 1


def test_cache_lifetime_when_no_expires_at(tmp_path: Path) -> None:
    src = _CountingSource(value="v1", expires_at=None)
    cfg = Config.load_from([_yaml(tmp_path, "k: ${secret:ctr:foo}\n"), src])

    for _ in range(5):
        cfg.get_string("k")
    assert len(src.resolve_calls) == 1


# ─── concurrency: refresh from another thread ─────────────────────────────


def test_refresh_safe_under_concurrent_reads(tmp_path: Path) -> None:
    import threading

    src = _CountingSource(value="v1")
    cfg = Config.load_from([_yaml(tmp_path, "k: ${secret:ctr:foo}\n"), src])

    stop = threading.Event()
    seen: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            seen.append(cfg.get_string("k"))

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for _ in range(20):
        cfg.refresh_secrets()
    stop.set()
    for t in threads:
        t.join()

    # Every observed value must be a valid resolution (never None / partial).
    assert all(v == "v1" for v in seen)


# ─── Type check that the SecretSource stub satisfies the Protocol ─────────


def _accepts_source(_src: SecretSource) -> None:
    pass


def test_counting_source_satisfies_secret_source_protocol() -> None:
    _accepts_source(_CountingSource())  # static + runtime structural check
    # Smoke-test the Protocol is runtime-checkable:
    assert isinstance(_CountingSource(), SecretSource)


# ─── _ctx unused suppression for stricter test runs ────────────────────────


def test_resolve_context_passes_through(tmp_path: Path) -> None:
    """ResolveContext defaults are honoured; just confirm the loader
    passes a fresh instance into every resolve() call."""
    captured: list[Any] = []

    class _Capture:
        @property
        def scheme(self) -> str:
            return "cap"

        @property
        def id(self) -> str:
            return "cap:test"

        def resolve(self, path: str, ctx: ResolveContext) -> SecretValue:
            captured.append((path, ctx))
            return SecretValue(value="ok", source_id="cap:test")

        def close(self) -> None:
            pass

    src = _Capture()
    cfg = Config.load_from([_yaml(tmp_path, "k: ${secret:cap:p}\n"), src])
    cfg.get_string("k")
    assert captured[0][0] == "p"
    assert isinstance(captured[0][1], ResolveContext)
