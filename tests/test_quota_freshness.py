"""Свежесть кэша и запасных снимков квот (ревью CLA-12: codex#2, grok#1, antigravity#1, quotas#7)."""
import datetime as dt

from claude_usage_manager import quota_monitor_model as model
from claude_usage_manager.database import Database
from claude_usage_manager.models import ManualResetCredits, ManualResetItem, QuotaSnapshot, QuotaWindow

UTC = dt.timezone.utc


def _codex(used, reset, *, credits=None):
    return QuotaSnapshot(
        provider="codex", source="codex-app-server:account/rateLimits/read", main_window_id="primary",
        windows=[QuotaWindow("primary", "Недельная квота", used, resets_at=reset, window_minutes=10080,
                             role="primary")],
        manual_resets=credits or ManualResetCredits(provider="codex"),
    )


def test_codex_cache_drops_window_after_its_reset(tmp_path):
    db = Database(tmp_path / "usage.db")
    try:
        now = dt.datetime.now(UTC)
        assert db.record_quota(_codex(40.0, now - dt.timedelta(minutes=5)))
        cached = model.cached_snapshot(db, "codex")
        assert not cached.has_data()
        assert cached.error
        assert db.record_quota(_codex(41.0, now + dt.timedelta(days=2)))
        assert model.cached_snapshot(db, "codex").weekly == 41.0
    finally:
        db.close()


def test_codex_cache_drops_expired_reset_credits(tmp_path):
    db = Database(tmp_path / "usage.db")
    try:
        now = dt.datetime.now(UTC)
        credits = ManualResetCredits(
            provider="codex", state="available", available_count=2, freshness="live",
            items=[ManualResetItem("old", "codex", now - dt.timedelta(hours=1)),
                   ManualResetItem("new", "codex", now + dt.timedelta(days=3))],
        )
        assert db.record_quota(_codex(10.0, now + dt.timedelta(days=2), credits=credits))
        cached = model.cached_snapshot(db, "codex").manual_resets
        assert [item.id for item in cached.items] == ["new"]
        assert cached.available_count == 1
    finally:
        db.close()


def test_expired_first_credit_does_not_hide_next_one():
    now = dt.datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    credits = ManualResetCredits(
        provider="codex", state="available", available_count=2,
        items=[ManualResetItem("gone", "codex", now - dt.timedelta(hours=2)),
               ManualResetItem("used", "codex", now + dt.timedelta(hours=1), status="used"),
               ManualResetItem("next", "codex", now + dt.timedelta(hours=20))],
    )
    assert credits.nearest_expiry(now) == now + dt.timedelta(hours=20)


def test_stale_fallback_does_not_replace_newer_measurement(tmp_path, monkeypatch):
    db = Database(tmp_path / "usage.db")
    try:
        now = dt.datetime.now(UTC)
        reset = now + dt.timedelta(days=3)

        def grok(used, observed):
            return QuotaSnapshot(
                provider="grok", source="grok:local-billing-log", login_method="Grok Build",
                main_window_id="grok:weekly", updated_at=observed,
                windows=[QuotaWindow("grok:weekly", "Grok", used, resets_at=reset, window_minutes=10080,
                                     role="group", details={"observed_at": observed.isoformat()})],
            )

        assert db.record_quota(grok(30.0, now - dt.timedelta(minutes=1)))
        stale = grok(12.0, now - dt.timedelta(hours=3))
        stale.fallback_source = "Локальный журнал Grok"
        monkeypatch.setattr(model, "fetch_quota", lambda provider: stale)
        state = model.refresh_provider(db, "grok")
        assert state.snapshot.weekly == 30.0
        assert not state.live
        rows = db.quota_samples(provider="grok")
        assert [row["windows"][0].used_percent for row in rows] == [30.0]
    finally:
        db.close()


def test_fallback_is_recorded_with_its_measurement_time(tmp_path):
    db = Database(tmp_path / "usage.db")
    try:
        observed = dt.datetime.now(UTC) - dt.timedelta(minutes=30)
        assert db.record_quota(_codex(5.0, observed + dt.timedelta(days=2)), observed_at=observed)
        [row] = db.quota_samples(provider="codex")
        assert row["ts_utc"] == observed.isoformat()
        # Более старое наблюдение после него не записывается.
        assert not db.record_quota(_codex(4.0, observed + dt.timedelta(days=2)),
                                   observed_at=observed - dt.timedelta(minutes=5))
    finally:
        db.close()
