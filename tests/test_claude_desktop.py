"""Read-only regressions for offline Claude Desktop plan-utilization samples."""
import datetime as dt
import json

from claude_usage_manager import claude_desktop, claude_local
from claude_usage_manager.backend_client import read_monitor_data
from claude_usage_manager.database import Database
from claude_usage_manager.quota import fetch_quota
from claude_usage_manager.quota_monitor_model import refresh_states

UTC = dt.timezone.utc


def sample(now, *, age_min=2, org="account-a", fh=1, sd=0, include_week=True):
    values = {"fh": fh}
    if include_week:
        values["sd"] = sd
    return {
        "t": (now - dt.timedelta(minutes=age_min)).timestamp() * 1000,
        "org": org,
        "u": values,
    }


def history(path, samples, version=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version, "samples": samples}), encoding="utf-8")
    return path


def test_5hour_and_weekly_zero_are_actual_quota_not_missing(tmp_path):
    now = dt.datetime.now(UTC)
    path = history(tmp_path / "plan-usage-history.json", [sample(now, fh=0, sd=0)])
    quota = claude_desktop.read_desktop_quota(paths=(path,), now=now)
    assert quota.has_data()
    assert quota.source == claude_desktop.SOURCE
    assert quota.five_hour == 0
    assert quota.weekly == 0
    assert [(w.id, w.window_minutes, w.used_percent) for w in quota.windows] == [
        ("primary", 300, 0.0), ("secondary", 10080, 0.0),
    ]
    assert quota.five_hour_resets_at is None
    assert quota.weekly_resets_at is None
    assert quota.fallback_source


def test_desktop_reads_only_newest_account_and_never_fills_missing_from_old(tmp_path):
    now = dt.datetime.now(UTC)
    older = sample(now, age_min=10, org="old", fh=30, sd=20)
    current = sample(now, age_min=1, org="new", fh=2, include_week=False)
    path = history(tmp_path / "one.json", [current, older])
    q = claude_desktop.read_desktop_quota(paths=(path,), now=now)
    assert q.five_hour == 2
    assert q.weekly is None
    assert len(q.windows) == 1
    path = history(tmp_path / "one.json", [older, {"t": current["t"], "org": "new", "u": {}}])
    assert not claude_desktop.read_desktop_quota(paths=(path,), now=now).has_data()


def test_desktop_rejects_old_future_invalid_and_oversized(tmp_path):
    now = dt.datetime.now(UTC)
    path = tmp_path / "plan-usage-history.json"
    for row in [
        sample(now, age_min=130),
        sample(now, age_min=-3),
        sample(now, fh="invalid", sd=None),
        sample(now, fh=True, sd=float("nan")),
        {**sample(now), "org": ""},
    ]:
        history(path, [row])
        assert not claude_desktop.read_desktop_quota(paths=(path,), now=now).has_data()
    path.write_bytes(b"0" * (claude_desktop.MAX_BYTES + 1))
    assert not claude_desktop.read_desktop_quota(paths=(path,), now=now).has_data()


def test_desktop_stale_is_clearly_marked_and_never_network_live(tmp_path, monkeypatch):
    now = dt.datetime.now(UTC)
    path = history(tmp_path / "plan-usage-history.json", [sample(now, age_min=42, fh=3, sd=5)])
    monkeypatch.setattr(claude_desktop, "desktop_history_paths", lambda: (path,))
    q = fetch_quota("claude")
    assert q.source == claude_desktop.SOURCE
    assert (q.five_hour, q.weekly) == (3, 5)
    assert "устарел" in q.fallback_source.lower()
    db = Database(tmp_path / "usage.db")
    try:
        states = refresh_states(db)
        assert states["claude"].live is False
        assert not db.quota_samples(provider="claude")
        rows, connected = read_monitor_data(db, base_url="http://127.0.0.1:1")
        assert not connected
        claude = [r for r in rows.compact_rows if r.provider == "claude"]
        assert claude and claude[0].short.remaining == 97
        assert claude[0].long.remaining == 95
    finally:
        db.close()


def test_newest_valid_desktop_profile_wins(tmp_path):
    now = dt.datetime.now(UTC)
    a = history(tmp_path / "a.json", [sample(now, age_min=12, fh=6, sd=5)])
    b = history(tmp_path / "b.json", [sample(now, age_min=1, fh=2, sd=3)])
    q = claude_desktop.read_desktop_quota(paths=(a, b), now=now)
    assert q.five_hour == 2
    assert q.weekly == 3


def test_newest_whole_claude_observation_wins_without_mixing_resets(tmp_path, monkeypatch):
    now = dt.datetime.now(UTC)
    desktop_path = history(tmp_path / "desktop.json", [sample(now, age_min=1, fh=12, sd=13)])
    monkeypatch.setattr(claude_desktop, "desktop_history_paths", lambda: (desktop_path,))
    status_path = tmp_path / "statusline.json"
    monkeypatch.setattr(claude_local, "statusline_path", lambda: status_path)
    payload = {"rate_limits": {"five_hour": {
        "used_percentage": 10,
        "resets_at": (now + dt.timedelta(hours=2)).timestamp(),
    }, "seven_day": {
        "used_percentage": 11,
        "resets_at": (now + dt.timedelta(days=2)).timestamp(),
    }}}
    assert claude_local.ingest_statusline(payload, path=status_path, now=now - dt.timedelta(minutes=4))
    q = claude_desktop.read_best_local_claude_quota()
    assert q.source == claude_desktop.SOURCE
    assert (q.five_hour, q.weekly) == (12, 13)
    assert q.five_hour_resets_at is None and q.weekly_resets_at is None
    assert claude_local.ingest_statusline(payload, path=status_path, now=now)
    q = claude_desktop.read_best_local_claude_quota()
    assert q.source == claude_local.SOURCE
    assert q.five_hour == 10 and q.weekly == 11


def test_desktop_history_read_does_not_modify_original(tmp_path):
    now = dt.datetime.now(UTC)
    path = history(tmp_path / "desktop.json", [sample(now, age_min=1, fh=3, sd=1)])
    before = path.read_bytes()
    assert claude_desktop.read_desktop_quota(paths=(path,), now=now).has_data()
    assert path.read_bytes() == before


def test_widget_uses_real_observation_not_newer_sqlite_collection_time(tmp_path, monkeypatch):
    from claude_usage_manager.models import QuotaSnapshot, QuotaWindow
    from claude_usage_manager.quota_monitor_model import cached_states

    now = dt.datetime.now(UTC)
    path = history(tmp_path / "history.json", [sample(now, age_min=7, fh=2, sd=4)])
    monkeypatch.setattr(claude_desktop, "desktop_history_paths", lambda: (path,))
    db = Database(tmp_path / "quota.db")
    try:
        db.record_quota(QuotaSnapshot(
            provider="claude", source=claude_local.SOURCE,
            main_window_id="secondary",
            windows=[QuotaWindow(
                "secondary", "Недельная квота", 90,
                resets_at=now + dt.timedelta(days=6), window_minutes=10080,
            )],
        ))
        state = cached_states(db)["claude"]
        assert state.snapshot.source == claude_desktop.SOURCE
        assert state.snapshot.five_hour == 2
        assert state.snapshot.weekly == 4
        assert not state.live
        assert state.snapshot.updated_at < now
    finally:
        db.close()


def test_widget_does_not_reanimate_old_sqlite_when_sources_expire(tmp_path):
    from claude_usage_manager.models import QuotaSnapshot, QuotaWindow
    from claude_usage_manager.quota_monitor_model import cached_states

    db = Database(tmp_path / "quota.db")
    try:
        assert db.record_quota(QuotaSnapshot(
            provider="claude", source=claude_local.SOURCE,
            main_window_id="secondary",
            windows=[QuotaWindow("secondary", "Неделя", 10,
                                 window_minutes=10080)],
        ))
        state = cached_states(db)["claude"]
        from claude_usage_manager.claude_mod_bridge import SOURCE as MOD_SOURCE
        assert state.snapshot.source == MOD_SOURCE
        assert not state.snapshot.has_data()
        assert not state.live
    finally:
        db.close()
