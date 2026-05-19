"""Unit tests for the Subscription handle."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from dagstack.config.subscription import (
    Subscription,
    emit_subscription_without_watch_warning,
)

if TYPE_CHECKING:
    import pytest


class TestSubscription:
    def test_active_false_by_default(self) -> None:
        sub = Subscription(path="x", active=False, inactive_reason="no watch")
        assert sub.active is False
        assert sub.inactive_reason == "no watch"
        assert sub.path == "x"

    def test_active_true_with_unsubscribe_impl(self) -> None:
        calls = []
        sub = Subscription(path="y", active=True, unsubscribe=lambda: calls.append("x"))
        assert sub.active is True
        sub.unsubscribe()
        assert calls == ["x"]

    def test_unsubscribe_idempotent(self) -> None:
        calls = []
        sub = Subscription(path="y", active=True, unsubscribe=lambda: calls.append("x"))
        sub.unsubscribe()
        sub.unsubscribe()
        sub.unsubscribe()
        assert calls == ["x"]

    def test_inactive_subscription_unsubscribe_no_op(self) -> None:
        sub = Subscription(path="x", active=False)
        # Must not raise — simply a no-op.
        sub.unsubscribe()
        sub.unsubscribe()

    def test_repr(self) -> None:
        sub = Subscription(path="llm.base_url", active=False, inactive_reason="foo")
        r = repr(sub)
        assert "llm.base_url" in r
        assert "active=False" in r
        assert "foo" in r


class TestWarningEmission:
    def test_warning_emitted_to_internal_logger(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="dagstack.config.internal"):
            emit_subscription_without_watch_warning(
                path="llm.base_url",
                source_ids=["yaml:/etc/config.yaml"],
            )
        assert any("subscription_without_watch" in record.message for record in caplog.records)
        assert any("llm.base_url" in record.message for record in caplog.records)
