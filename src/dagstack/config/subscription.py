"""Subscription handle for config change callbacks.

Per spec ADR-0001 §7.2:
- `Subscription { unsubscribe(), active, inactive_reason, path }`.
- In Phase 1 `active=false` for all subscriptions — no source supports
  `watch()`, so the callback never fires.
- The loader must emit a `subscription_without_watch` warning on the
  diagnostic channel when a subscription is registered without a
  watch-capable source.

The diagnostic channel is a named logger `dagstack.config.internal`
(mirroring the logger-spec §7.4 isolation requirement — but logger-spec
is not yet implemented, so we use the standard Python `logging`).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_INTERNAL_LOGGER = logging.getLogger("dagstack.config.internal")


class Subscription:
    """Subscription handle returned by `on_change` / `on_section_change`.

    Contract:
    - `unsubscribe()` is idempotent: subsequent calls are no-ops.
    - After `unsubscribe()` the callback is guaranteed not to be invoked.
    - `active=False` signals that the callback will NOT fire — either
      because there is no watch-capable source, or the path scope is not
      covered by any active source. This is not an error but an honest
      contract signal (see `inactive_reason`).
    """

    __slots__ = ("_unsubscribe_impl", "_unsubscribed", "active", "inactive_reason", "path")

    path: str
    active: bool
    inactive_reason: str | None
    _unsubscribe_impl: Callable[[], None]
    _unsubscribed: bool

    def __init__(
        self,
        *,
        path: str,
        active: bool,
        inactive_reason: str | None = None,
        unsubscribe: Callable[[], None] | None = None,
    ) -> None:
        self.path = path
        self.active = active
        self.inactive_reason = inactive_reason
        self._unsubscribe_impl = unsubscribe if unsubscribe is not None else _noop
        self._unsubscribed = False

    def unsubscribe(self) -> None:
        """Cancel subscription. Idempotent."""
        if self._unsubscribed:
            return
        self._unsubscribed = True
        self._unsubscribe_impl()

    def __repr__(self) -> str:
        return (
            f"Subscription(path={self.path!r}, active={self.active}, "
            f"inactive_reason={self.inactive_reason!r})"
        )


def _noop() -> None:
    """Default unsubscribe for inactive subscriptions."""


def emit_subscription_without_watch_warning(
    *,
    path: str,
    source_ids: list[str],
) -> None:
    """Emit a structured warning when a subscription is registered without a watch-capable source.

    Per spec §7.2: warning code `subscription_without_watch`, payload is
    the path and the registered source_ids (none with watch capability).
    """
    _INTERNAL_LOGGER.warning(
        "subscription_without_watch: path=%r source_ids=%r — callback will never fire "
        "(no watch-capable source registered)",
        path,
        source_ids,
    )
