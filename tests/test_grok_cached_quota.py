"""Protect current Grok quota from expired and legacy SQLite samples."""
from __future__ import annotations

import datetime as dt

from claude_usage_manager.database import Database
from claude_usage_manager.models import QuotaSnapshot, QuotaWindow
from claude_usage_manager.quota_monitor_model import cached_snapshot


def record(db, source, reset):
    value = QuotaSnapshot(
        provider="grok", source=source, weekly=17,
        main_window_id="grok:weekly", windows=[
            QuotaWindow("grok:weekly", "Grok weekly", 17,
                        resets_at=reset, window_minutes=10080, role="group")
        ],
    )
    assert db.record_quota(value)


def test_legacy_grok_history_never_becomes_live_cache(tmp_path):
    db = Database(tmp_path / "quota.db")
    record(db, "codexbar:grok", dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3))
    assert db.quota_samples(provider="grok") == []
    current = cached_snapshot(db, "grok")
    assert current.source == "grok:local-billing-log"
    assert not current.has_data()
    db.close()


def test_expired_grok_weekly_cache_is_rejected(tmp_path):
    db = Database(tmp_path / "quota.db")
    record(db, "grok:local-billing-log", dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc))
    current = cached_snapshot(db, "grok")
    assert not current.has_data()
    assert "актуального" in current.error
    db.close()


def test_current_week_grok_cache_preserved_without_wrong_reset(tmp_path):
    db = Database(tmp_path / "quota.db")
    reset = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=6)
    record(db, "grok:local-billing-log", reset)
    current = cached_snapshot(db, "grok")
    assert current.has_data()
    assert current.weekly == 17
    assert current.weekly_resets_at == reset
    assert current.updated_at is None
    db.close()


def test_cached_grok_uses_observation_time_not_database_write_time(tmp_path):
    db = Database(tmp_path / "quota.db")
    observed = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    reset = observed + dt.timedelta(days=4)
    value = QuotaSnapshot(provider="grok", source="grok:local-billing-log",
                          main_window_id="grok:weekly", windows=[
                              QuotaWindow("grok:weekly", "Grok", 17, resets_at=reset,
                                          window_minutes=10080, details={"observed_at": observed.isoformat()})])
    db.record_quota(value)
    assert cached_snapshot(db, "grok").updated_at == observed
    db.close()
