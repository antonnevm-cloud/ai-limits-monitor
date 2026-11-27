"""Native Grok Build weekly quota, using only synthetic local log fixtures."""
from __future__ import annotations

import datetime as dt
import json

from claude_usage_manager.grok_provider import fetch_grok_quota, parse_billing_event


def event(start, end, observed, percent=7.25, unified=True, period_type="USAGE_PERIOD_TYPE_WEEKLY"):
    return json.dumps({
        "ts": observed.isoformat(),
        "msg": "billing: fetched credits config",
        "ctx": {
            "subscriptionTier": "SuperGrok",
            "config": {
                "isUnifiedBillingUser": unified,
                "creditUsagePercent": percent,
                "currentPeriod": {"type": period_type, "start": start.isoformat(), "end": end.isoformat()},
            },
        },
    })


def test_native_weekly_source_and_reset(tmp_path):
    now = dt.datetime(2026, 11, 8, 13, 0, tzinfo=dt.timezone.utc)
    start = now - dt.timedelta(hours=4)
    end = start + dt.timedelta(days=7)
    log = tmp_path / "unified.jsonl"
    log.write_text(event(start, end, now - dt.timedelta(minutes=1)), encoding="utf-8")
    result = fetch_grok_quota(log, now=now)
    assert result.provider == "grok"
    assert result.source == "grok:local-billing-log"
    assert result.weekly == 7.25
    assert result.weekly_resets_at == end
    assert result.main_window_id == "grok:weekly"
    assert result.window("grok:weekly").window_minutes == 10080
    assert result.fallback_source == ""


def test_stale_snapshot_is_identified_not_live(tmp_path):
    now = dt.datetime(2026, 11, 8, 13, 0, tzinfo=dt.timezone.utc)
    start = now - dt.timedelta(days=1)
    log = tmp_path / "unified.jsonl"
    log.write_text(event(start, start + dt.timedelta(days=7), now - dt.timedelta(hours=1)), encoding="utf-8")
    result = fetch_grok_quota(log, now=now)
    assert result.has_data()
    assert "устарел" in result.fallback_source


def test_expired_and_invalid_events_never_display_as_live(tmp_path):
    now = dt.datetime(2026, 11, 8, 13, 0, tzinfo=dt.timezone.utc)
    start = now - dt.timedelta(days=8)
    log = tmp_path / "unified.jsonl"
    log.write_text(
        event(start, start + dt.timedelta(days=7), now - dt.timedelta(days=2)) + "\n"
        + event(now, now + dt.timedelta(days=7), now, percent=101) + "\n"
        + event(now, now + dt.timedelta(days=7), now, unified=False),
        encoding="utf-8",
    )
    result = fetch_grok_quota(log, now=now)
    assert not result.has_data()
    assert result.error
    assert parse_billing_event("not JSON", now) is None
