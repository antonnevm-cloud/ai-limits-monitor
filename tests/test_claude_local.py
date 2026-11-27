"""Regression checks for Anthropic-compliant local Claude quota integration."""
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

import pytest

from claude_usage_manager.claude_local import (
    MANUAL_SOURCE, SOURCE, ingest_statusline, install_statusline_command,
    manual_resets_edit_text, parse_manual_resets, read_manual_resets,
    read_statusline_quota, save_manual_resets,
)
from claude_usage_manager.database import Database
from claude_usage_manager.models import QuotaSnapshot, QuotaWindow
from claude_usage_manager.quota_monitor_model import cached_snapshot, manual_reset_tooltip

UTC = dt.timezone.utc


def payload(now, five=23.5, seven=41.2):
    return {
        "session_id": "NEVER-PERSIST",
        "transcript_path": "NEVER-PERSIST",
        "rate_limits": {
            "five_hour": {
                "used_percentage": five,
                "resets_at": (now + dt.timedelta(hours=3)).timestamp(),
            },
            "seven_day": {
                "used_percentage": seven,
                "resets_at": (now + dt.timedelta(days=4)).timestamp(),
            },
        },
    }


def test_statusline_retains_only_allowlisted_fields_and_both_windows(tmp_path):
    now = dt.datetime.now(UTC)
    path = tmp_path / "usage.json"
    data = payload(now)
    assert ingest_statusline(data, path=path, now=now)
    stored = path.read_text(encoding="utf-8")
    assert "NEVER-PERSIST" not in stored
    assert set(json.loads(stored)) == {"schema_version", "received_at", "windows"}
    snapshot = read_statusline_quota(path=path, now=now)
    assert snapshot.source == SOURCE
    assert snapshot.five_hour == 23.5
    assert snapshot.weekly == 41.2
    assert snapshot.five_hour_resets_at > now
    assert snapshot.weekly_resets_at > now
    assert [w.id for w in snapshot.windows] == ["primary", "secondary"]
    assert snapshot.fallback_source == ""


def test_zero_consumption_is_a_valid_live_quota(tmp_path):
    from claude_usage_manager.quota_monitor_model import remaining

    now = dt.datetime.now(UTC)
    target = tmp_path / "zero-usage.json"
    assert ingest_statusline(payload(now, five=0, seven=0), path=target, now=now)
    state = read_statusline_quota(path=target, now=now)
    assert state.has_data()
    assert state.five_hour == 0.0
    assert state.weekly == 0.0
    assert len(state.windows) == 2
    assert all(remaining(window) == 100.0 for window in state.windows)


def test_statusline_absence_invalid_data_and_subscriber_without_quota_are_safe(tmp_path):
    now = dt.datetime.now(UTC)
    path = tmp_path / "usage.json"
    assert not read_statusline_quota(path=path, now=now).has_data()
    assert not ingest_statusline({"rate_limits": {}}, path=path, now=now)
    assert not path.exists()
    assert ingest_statusline(payload(now), path=path, now=now)
    before = path.read_bytes()
    assert not ingest_statusline({"rate_limits": {"five_hour": {
        "used_percentage": float("nan")
    }}}, path=path, now=now)
    assert not ingest_statusline({"rate_limits": {"five_hour": {
        "used_percentage": True
    }}}, path=path, now=now)
    assert path.read_bytes() == before


def test_statusline_stale_and_expired_windows_are_not_falsely_live(tmp_path):
    now = dt.datetime.now(UTC)
    path = tmp_path / "usage.json"
    assert ingest_statusline(payload(now), path=path, now=now - dt.timedelta(minutes=15))
    snapshot = read_statusline_quota(path=path, now=now)
    assert snapshot.has_data()
    assert snapshot.fallback_source
    assert not read_statusline_quota(path=path, now=now + dt.timedelta(days=2)).has_data()
    expired = payload(now)
    expired["rate_limits"]["five_hour"]["resets_at"] = (now - dt.timedelta(minutes=1)).timestamp()
    assert ingest_statusline(expired, path=path, now=now)
    quota = read_statusline_quota(path=path, now=now)
    assert quota.five_hour is None
    assert quota.weekly == 41.2


def test_old_browser_quota_never_passes_as_new_account(tmp_path):
    db = Database(tmp_path / "usage.db")
    try:
        historical = QuotaSnapshot(
            provider="claude", source="codexbar:claude",
            windows=[QuotaWindow("secondary", "Неделя", 88.0,
                                 window_minutes=10080, role="secondary")],
            main_window_id="secondary",
        )
        assert db.record_quota(historical)
        assert len(db.quota_samples(provider="claude")) == 1
        assert not cached_snapshot(db, "claude").has_data()
        fresh = QuotaSnapshot(
            provider="claude", source=SOURCE,
            windows=[QuotaWindow("secondary", "Неделя", 11.0,
                                 resets_at=dt.datetime.now(UTC) + dt.timedelta(days=1),
                                 window_minutes=10080, role="secondary")],
            main_window_id="secondary",
        )
        assert db.record_quota(fresh)
        assert cached_snapshot(db, "claude").weekly == 11.0
    finally:
        db.close()


def test_manual_offers_keep_each_expiry_and_expire_without_network(tmp_path):
    db = Database(tmp_path / "usage.db")
    try:
        now = dt.datetime.now(UTC)
        first = (now + dt.timedelta(days=2)).astimezone().strftime("%Y-%m-%d %H:%M")
        second = (now + dt.timedelta(days=6)).astimezone().strftime("%Y-%m-%d %H:%M")
        assert save_manual_resets(db, first + "\n" + second + "\n-", now=now) == 3
        state = read_manual_resets(db, now=now)
        assert state.source == MANUAL_SOURCE
        assert state.freshness == "manual"
        assert state.available_count == 3
        assert len(state.items) == 3
        tip = manual_reset_tooltip(state, now=now)
        assert "Введено вручную" in tip
        assert first[0:4] in tip
        assert manual_resets_edit_text(db).count("\n") == 2
        later = read_manual_resets(db, now=now + dt.timedelta(days=3))
        assert later.available_count == 2
        assert len(later.items) == 2
        assert read_manual_resets(db, now=now + dt.timedelta(days=15)).available_count == 1
        assert save_manual_resets(db, "", now=now) == 0
        assert read_manual_resets(db, now=now).available_count == 0
    finally:
        db.close()


def test_manual_offers_unknown_until_user_enters_data_and_input_errors(tmp_path):
    db = Database(tmp_path / "usage.db")
    try:
        assert read_manual_resets(db).state == "unknown"
        assert cached_snapshot(db, "claude").manual_resets.available_count is None
        with pytest.raises(ValueError):
            parse_manual_resets("bad-date")
        with pytest.raises(ValueError):
            parse_manual_resets("2001-01-01 00:00")
        with pytest.raises(ValueError):
            parse_manual_resets("-\n" * 31)
        assert read_manual_resets(db).state == "unknown"
    finally:
        db.close()


def test_statusline_setup_respects_custom_settings_and_preserves_other_keys(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "opus", "statusLine": {
        "type": "command", "command": "custom-status"
    }}), encoding="utf-8")
    before = settings.read_bytes()
    assert install_statusline_command('"C:/ClaudeUsageManager/monitor.py" --claude-statusline',
                                      settings_path=settings) == "conflict"
    assert settings.read_bytes() == before
    settings.write_text(json.dumps({"statusLine": {
        "type": "command", "command": '"C:/third-party/monitor.exe" --claude-statusline'
    }}), encoding="utf-8")
    assert install_statusline_command('"C:/ClaudeUsageManager/monitor.py" --claude-statusline',
                                      settings_path=settings) == "conflict"
    settings.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    assert install_statusline_command('"C:/ClaudeUsageManager/monitor.py" --claude-statusline',
                                      settings_path=settings) == "installed"
    assert json.loads(settings.read_text(encoding="utf-8"))["model"] == "opus"
    assert (tmp_path / "settings.json.ai-limits-backup").is_file()
    assert install_statusline_command('"C:/ClaudeUsageManager/monitor.py" --claude-statusline',
                                      settings_path=settings) == "already"
    assert install_statusline_command('"C:/new/ClaudeUsageManager/monitor.py" --claude-statusline',
                                      settings_path=settings) == "installed"


def test_statusline_entrypoint_runs_without_gui_and_accepts_documented_json(tmp_path):
    now = dt.datetime.now(UTC)
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(tmp_path)
    env["AI_LIMITS_DATA_DIR"] = str(tmp_path / "ClaudeUsageManager")
    process = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "monitor.py"),
         "--claude-statusline"],
        input=json.dumps(payload(now)), capture_output=True, text=True,
        encoding="utf-8", env=env, timeout=15,
    )
    assert process.returncode == 0, process.stderr
    assert "5h: 24%" in process.stdout
    result = tmp_path / "ClaudeUsageManager" / "claude-statusline-v1.json"
    assert result.is_file()
    assert "NEVER-PERSIST" not in result.read_text(encoding="utf-8")


def test_gui_pythonw_statusline_bridge_uses_console_python(monkeypatch, tmp_path):
    from claude_usage_manager import claude_local
    monkeypatch.delenv("AI_LIMITS_DATA_DIR", raising=False)
    interpreter = tmp_path / "python.exe"
    interpreter.touch()
    monkeypatch.setattr(claude_local.sys, "argv", ["monitor.py"])
    monkeypatch.setattr(claude_local.sys, "executable", str(tmp_path / "pythonw.exe"))
    command = claude_local.bridge_command()
    assert '"'+interpreter.as_posix()+'"' in command
    assert 'pythonw.exe' not in command.casefold()
    assert command.endswith(' --claude-statusline')
