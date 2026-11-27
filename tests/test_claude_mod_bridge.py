"""Claude Code Mods official event quota: exact windows, no Anthropic requests."""
import datetime as dt
import json

from claude_usage_manager import claude_local, claude_desktop, claude_mod_bridge
from claude_usage_manager.backend_client import read_monitor_data
from claude_usage_manager.database import Database
from claude_usage_manager.quota import fetch_quota
from claude_usage_manager.quota_monitor_model import cached_states, refresh_states

UTC = dt.timezone.utc


def write_mod(path, *, stamped=None, five=6, weekly=12,
              reset5=None, reset7=None):
    now = dt.datetime.now(UTC)
    data = {
        "schema_version": 1,
        "received_at": (stamped or now).isoformat(),
        "windows": {
            "five_hour": {
                "used_percentage": five,
                "resets_at": (reset5 or (now + dt.timedelta(hours=4))).timestamp(),
            },
            "seven_day": {
                "used_percentage": weekly,
                "resets_at": (reset7 or (now + dt.timedelta(days=3))).timestamp(),
            },
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_mod_quota_retains_both_exact_reset_times(tmp_path):
    now = dt.datetime.now(UTC)
    path = tmp_path / "claude-mod-quota-v1.json"
    five_reset = now + dt.timedelta(hours=3)
    week_reset = now + dt.timedelta(days=2)
    write_mod(path, stamped=now, five=6, weekly=12,
              reset5=five_reset, reset7=week_reset)

    quota = claude_mod_bridge.read_mod_quota(path=path, now=now)
    assert quota.source == claude_mod_bridge.SOURCE
    assert quota.login_method.startswith("Claude Code")
    assert quota.five_hour == 6 and quota.weekly == 12
    assert [w.id for w in quota.windows] == ["primary", "secondary"]
    assert [w.window_minutes for w in quota.windows] == [300, 10080]
    assert abs((quota.five_hour_resets_at - five_reset).total_seconds()) < .01
    assert abs((quota.weekly_resets_at - week_reset).total_seconds()) < .01
    assert quota.main_window_id == "secondary"
    assert not quota.fallback_source


def test_mod_zero_usage_is_a_real_window_with_100_percent_remaining(tmp_path):
    from claude_usage_manager.quota_monitor_model import build_rows, ProviderState
    now = dt.datetime.now(UTC)
    path = tmp_path / "claude-mod-quota-v1.json"
    write_mod(path, stamped=now, five=0, weekly=0)
    quota = claude_mod_bridge.read_mod_quota(path=path, now=now)
    row = build_rows({"claude": ProviderState("claude", quota, True)})["claude"][0]
    assert row.short.remaining == 100
    assert row.long.remaining == 100
    assert row.short.resets_at is not None
    assert row.long.resets_at is not None


def test_mod_newest_outweighs_desktop_percent_only_and_statusline(tmp_path, monkeypatch):
    now = dt.datetime.now(UTC)
    mod = tmp_path / "mod.json"
    status = tmp_path / "status.json"
    history = tmp_path / "desktop.json"

    write_mod(mod, stamped=now, five=6, weekly=12)
    assert claude_local.ingest_statusline({
        "rate_limits": {
            "five_hour": {"used_percentage": 4},
            "seven_day": {"used_percentage": 11},
        }
    }, path=status, now=now - dt.timedelta(minutes=4))
    history.write_text(json.dumps({
        "version": 2,
        "samples": [{"t": (now-dt.timedelta(minutes=2)).timestamp()*1000,
                     "org": "account-current",
                     "u": {"fh": 5, "sd": 11}}]
    }), encoding="utf-8")
    monkeypatch.setattr(claude_mod_bridge, "mod_snapshot_path", lambda: mod)
    monkeypatch.setattr(claude_local, "statusline_path", lambda: status)
    monkeypatch.setattr(claude_desktop, "desktop_history_paths", lambda: (history,))

    result = fetch_quota("claude")
    assert result.source == claude_mod_bridge.SOURCE
    assert (result.five_hour, result.weekly) == (6, 12)
    assert all(w.resets_at is not None for w in result.windows)
    assert all(w.resets_at is not None for w in
               claude_desktop.read_best_local_claude_quota().windows)


def test_newer_desktop_measurement_takes_precedence_over_mod(tmp_path, monkeypatch):
    now = dt.datetime.now(UTC)
    mod = tmp_path / "mod.json"
    history = tmp_path / "history.json"
    write_mod(mod, stamped=now-dt.timedelta(minutes=3), five=6, weekly=12)
    history.write_text(json.dumps({
        "version": 2,
        "samples": [{"t": (now-dt.timedelta(seconds=10)).timestamp()*1000,
                     "org": "account-current", "u": {"fh": 5, "sd": 11}}],
    }), encoding="utf-8")
    monkeypatch.setattr(claude_mod_bridge, "mod_snapshot_path", lambda: mod)
    monkeypatch.setattr(claude_desktop, "desktop_history_paths", lambda: (history,))
    quota = fetch_quota("claude")
    assert quota.source == claude_desktop.SOURCE
    assert (quota.five_hour, quota.weekly) == (5, 11)
    assert all(window.resets_at is None for window in quota.windows)


def test_mod_stale_record_never_overrides_newer_documented_event(tmp_path, monkeypatch):
    now = dt.datetime.now(UTC)
    mod = tmp_path / "mod.json"
    status = tmp_path / "status.json"
    write_mod(mod, stamped=now - dt.timedelta(hours=3), five=44, weekly=20)
    assert claude_local.ingest_statusline({
        "rate_limits": {
            "five_hour": {"used_percentage": 7,
                          "resets_at": (now+dt.timedelta(hours=2)).timestamp()},
            "seven_day": {"used_percentage": 13,
                          "resets_at": (now+dt.timedelta(days=2)).timestamp()},
        },
    }, path=status, now=now - dt.timedelta(minutes=2))
    monkeypatch.setattr(claude_mod_bridge, "mod_snapshot_path", lambda: mod)
    monkeypatch.setattr(claude_local, "statusline_path", lambda: status)
    quota = fetch_quota("claude")
    assert quota.source == claude_local.SOURCE
    assert quota.five_hour == 7 and quota.weekly == 13


def test_mod_bad_or_old_file_falls_back_without_network(tmp_path, monkeypatch):
    mod = tmp_path / "mod.json"
    monkeypatch.setattr(claude_mod_bridge, "mod_snapshot_path", lambda: mod)
    for invalid in [
        "{}",
        '{"schema_version":1,"received_at":"invalid","windows":{}}',
        '{"schema_version":1,"received_at":"2020-01-01T00:00:00+00:00","windows":{"five_hour":{"used_percentage":20}}}',
    ]:
        mod.write_text(invalid, encoding="utf-8")
        assert not claude_mod_bridge.read_mod_quota().has_data()
    mod.unlink()
    assert not claude_mod_bridge.read_mod_quota().has_data()


def test_active_widget_reads_current_mod_and_reset_dates(tmp_path, monkeypatch):
    mod = tmp_path / "mod.json"
    write_mod(mod, five=6, weekly=12)
    monkeypatch.setattr(claude_mod_bridge, "mod_snapshot_path", lambda: mod)
    db = Database(tmp_path / "usage.db")
    try:
        states = cached_states(db)
        q = states["claude"].snapshot
        assert q.source == claude_mod_bridge.SOURCE
        assert q.five_hour == 6 and q.weekly == 12
        assert all(w.resets_at is not None for w in q.windows)
        data, connected = read_monitor_data(db, base_url="http://127.0.0.1:1")
        assert not connected
        row = next(row for row in data.compact_rows if row.provider == "claude")
        assert row.short.remaining == 94
        assert row.long.remaining == 88
        assert row.short.resets_at is not None
        assert row.long.resets_at is not None
        fresh = refresh_states(db)["claude"]
        assert fresh.snapshot.source == claude_mod_bridge.SOURCE
        assert fresh.snapshot.has_data()
        assert db.quota_samples(provider="claude")
    finally:
        db.close()


def test_local_mod_snapshot_does_not_leak_unrelated_fields(tmp_path):
    now = dt.datetime.now(UTC)
    mod = tmp_path / "mod.json"
    data = write_mod(mod, stamped=now)
    data.update({"session_id": "secret-session",
                 "transcript": "must not be shown",
                 "oauth": "secret"})
    mod.write_text(json.dumps(data), encoding="utf-8")
    result = claude_mod_bridge.read_mod_quota(path=mod, now=now)
    assert result.has_data()
    raw_repr = repr(result)
    assert "secret-session" not in raw_repr
    assert "must not be shown" not in raw_repr
    assert "secret" not in raw_repr
