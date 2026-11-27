"""Автозапуск, снятие интеграций при удалении, перенос ключа истории и поведение окна при запуске."""
import dataclasses
import datetime as dt
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from claude_usage_manager import autostart, claude_local, integrations
from claude_usage_manager import provider_settings as settings
from claude_usage_manager.database import Database, _same_windows
from claude_usage_manager.models import QuotaSnapshot, QuotaWindow
from claude_usage_manager.quota_monitor import MonitorWindow

# Настоящие функции до подмены фикстурой conftest (она не дает тестам писать в раздел Run профиля).
REAL_REGISTERED = autostart.registered
REAL_SET_ENABLED = autostart.set_enabled


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ, REG_EXPAND_SZ = 1, 2

    def __init__(self):
        self.values = {}

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def OpenKey(self, root, path):
        return self._Key()

    CreateKey = OpenKey

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        del self.values[name]


@pytest.fixture
def registry(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(autostart, "registered", REAL_REGISTERED)
    monkeypatch.setattr(autostart, "set_enabled", REAL_SET_ENABLED)
    monkeypatch.setattr(autostart, "supported", lambda: True)
    return fake


def test_autostart_switch_owns_only_its_own_copy(registry, tmp_path, monkeypatch):
    program = tmp_path / "Программа" / "AI Limits Monitor.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program))
    assert autostart.command() == f'"{program.resolve()}" --autostart'
    autostart.set_enabled(True)
    assert registry.values[autostart.VALUE_NAME] == autostart.command()
    assert autostart.is_enabled()
    autostart.set_enabled(False)
    assert autostart.VALUE_NAME not in registry.values
    # Автозапуск другой копии программы этот переключатель не снимает.
    registry.values[autostart.VALUE_NAME] = '"D:\\Другая копия\\AI Limits Monitor.exe" --autostart'
    assert not autostart.is_enabled()
    autostart.set_enabled(False)
    assert autostart.VALUE_NAME in registry.values


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: folder))
    for variable in settings.PROFILE_VARIABLES.values():
        monkeypatch.delenv(variable, raising=False)
    return folder


def test_uninstall_restores_previous_statusline_and_turns_autostart_off(home, _never_touch_real_autostart):
    profile = home / ".claude"
    profile.mkdir()
    target = profile / "settings.json"
    target.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    assert claude_local.install_statusline_command(claude_local.bridge_command(profile=profile),
                                                   settings_path=target) == "installed"
    _never_touch_real_autostart["value"] = autostart.command()
    assert integrations.uninstall_main() == 0
    restored = json.loads(target.read_text(encoding="utf-8"))
    assert "statusLine" not in restored and restored["theme"] == "dark"
    assert _never_touch_real_autostart["value"] is None


def test_uninstall_never_fails_the_uninstaller(monkeypatch):
    def broken():
        raise RuntimeError("сбой")

    monkeypatch.setattr(integrations, "remove_all", broken)
    assert integrations.uninstall_main() == 0


def test_history_written_with_old_profile_key_is_moved(home, tmp_path):
    program = tmp_path / "bin" / "codex.exe"
    program.parent.mkdir()
    program.touch()
    connections = settings.load_connections()
    connections["codex"] = dataclasses.replace(connections["codex"], executable=str(program))
    settings.save_connections(connections)
    old = settings.legacy_provider_config_key("codex")
    new = settings.provider_config_key("codex")
    assert old != new
    db = Database(tmp_path / "usage.db")
    try:
        snapshot = QuotaSnapshot(provider="codex", source="codex-app-server:account/rateLimits/read",
                                 windows=[QuotaWindow("primary", "Неделя", 30.0, window_minutes=10080)])
        assert db.record_quota(snapshot, profile_key=old)
        assert db.quota_samples(provider="codex") == []
        assert db.migrate_profile_keys() == 1
        assert len(db.quota_samples(provider="codex")) == 1
        assert db.migrate_profile_keys() == 0
    finally:
        db.close()


def _windows(used, reset, observed):
    return json.dumps([{"id": "w", "used_percent": used, "resets_at": reset.isoformat(),
                        "details": {"observed_at": observed}}])


def test_same_windows_ignores_observation_time_and_reset_jitter():
    reset = dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)
    first = _windows(30.0, reset, "10:00")
    assert _same_windows(first, _windows(30.0, reset + dt.timedelta(seconds=2), "10:05"))
    assert not _same_windows(first, _windows(31.0, reset, "10:00"))
    assert not _same_windows(first, _windows(30.0, reset + dt.timedelta(hours=5), "10:00"))


def _window(tmp_path):
    QApplication.instance() or QApplication([])
    db = Database(tmp_path / "ui.sqlite")
    return MonitorWindow(db, auto_refresh=False, cache_scan=False), db


def _stop(window, db):
    window.timer.stop()
    window._quit = True
    window.close()
    db.close()


def test_autostart_shows_the_widget_without_keeping_no_activate_flag(tmp_path):
    window, db = _window(tmp_path)
    try:
        window.show_without_activation()
        assert window.isVisible()
        assert not window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    finally:
        _stop(window, db)


def test_widget_is_not_hidden_when_there_is_no_tray_icon(tmp_path, monkeypatch):
    window, db = _window(tmp_path)
    try:
        monkeypatch.setattr(window, "_tray_ready", lambda: False)
        window.show()
        window.hide_to_tray()
        assert window.isVisible()  # спрятанное окно без значка вернуть было бы нечем
        window.close()
        assert window.isVisible()
    finally:
        _stop(window, db)
