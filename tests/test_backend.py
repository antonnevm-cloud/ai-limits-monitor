import json
import datetime as dt
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from claude_usage_manager.backend import BackendService, read_control
from claude_usage_manager.backend_client import read_monitor_data
from claude_usage_manager.database import Database
from claude_usage_manager.quota_monitor_model import cached_states, refresh_states
from claude_usage_manager.quota_monitor_model import ProviderState
from claude_usage_manager.models import QuotaSnapshot, QuotaWindow
from claude_usage_manager.local_api import LocalApiServer
import claude_usage_manager.quota_monitor_model as model


def test_backend_import_does_not_load_qt():
    result = subprocess.run([sys.executable, "-c", "import sys; import claude_usage_manager.backend; assert not any(m.startswith('PySide6') for m in sys.modules)"], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_disabled_claude_is_never_fetched_or_written(tmp_path, monkeypatch):
    db = Database(tmp_path / "usage.db")
    fetched, recorded = [], []
    def fetch(provider):
        assert provider != "claude"
        fetched.append(provider)
        return cached_states(db)[provider].snapshot
    monkeypatch.setattr(model, "fetch_quota", fetch)
    monkeypatch.setattr(db, "record_quota", lambda value: recorded.append(value.provider))
    states = refresh_states(db, excluded_providers=("claude",))
    assert fetched == ["codex", "grok", "antigravity"]
    assert "claude" not in recorded
    assert states["claude"].live is False
    db.close()


def test_backend_api_lifecycle_refresh_and_widget_offline(tmp_path):
    path = tmp_path / "usage.db"
    refreshed = threading.Event()
    calls = []
    def refresh(db):
        calls.append(True)
        refreshed.set()
        return cached_states(db)
    service = BackendService(port=0, db_factory=lambda: Database(path), refresh=refresh)
    db = Database(path)
    db.set_setting("kept_setting", "сохранено")
    service.start()
    try:
        assert refreshed.wait(3)
        with urllib.request.urlopen(service.api.base_url + "/health", timeout=3) as response:
            health = json.load(response)
        assert health["mode"] == "backend"
        assert health["service"] == "ai-limits-monitor"
        data, connected = read_monitor_data(db, base_url=service.api.base_url)
        assert connected and set(data.states) == {"claude", "codex", "grok", "antigravity"}
        refreshed.clear()
        request = urllib.request.Request(service.api.base_url + "/v1/refresh", data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=3) as response:
            assert response.status == 202
        assert refreshed.wait(3)
        assert len(calls) >= 2
        # Графический клиент не владеет сервером: закрытие его БД сервер не выключает.
        db.close()
        with urllib.request.urlopen(service.api.base_url + "/health", timeout=3) as response:
            assert response.status == 200
        url = service.api.base_url
    finally:
        service.stop()
    db = Database(path)
    data, connected = read_monitor_data(db, base_url=url)
    assert not connected and all(not state.live for state in data.states.values())
    assert db.get_setting("kept_setting") == "сохранено"
    db.close()


def test_control_missing_invalid_and_manual_stop(tmp_path):
    path = tmp_path / "control.json"
    assert read_control(path)["desired_running"] is False
    path.write_text("bad", encoding="utf-8")
    assert read_control(path)["desired_running"] is False
    path.write_text(json.dumps({"desired_running": False, "auto_start": True}), encoding="utf-8")
    assert read_control(path) == {"desired_running": False, "auto_start": True}


def test_slow_provider_does_not_delay_other_provider_updates(tmp_path):
    path = tmp_path / "quota.db"
    slow_started, release_slow, claude_updated, grok_again = (threading.Event() for _ in range(4))
    grok_calls = []

    def refresh_one(db, provider):
        if provider == "grok":
            grok_calls.append(1)
            if len(grok_calls) == 1:
                slow_started.set()
                assert release_slow.wait(5)
            else:
                grok_again.set()
        snapshot = QuotaSnapshot(provider=provider, source=f"test:{provider}",
                                 windows=[QuotaWindow(provider, provider, 12)])
        if provider == "claude":
            claude_updated.set()
        return ProviderState(provider, snapshot, True, "")

    service = BackendService(port=0, db_factory=lambda: Database(path), provider_refresh=refresh_one)
    service.start()
    try:
        assert slow_started.wait(3) and claude_updated.wait(3)
        for _ in range(100):
            if service.states()["claude"].snapshot.source == "test:claude":
                break
            threading.Event().wait(0.01)
        assert service.states()["claude"].snapshot.source == "test:claude"
        assert not release_slow.is_set()
        service.request_refresh()
        release_slow.set()
        assert grok_again.wait(3)
    finally:
        release_slow.set()
        service.stop()


def test_widget_uses_grok_backend_observation_instead_of_older_database(tmp_path):
    now = dt.datetime.now(dt.timezone.utc)
    reset = now + dt.timedelta(days=4)
    db = Database(tmp_path / "quota.db")
    old = QuotaSnapshot(provider="grok", source="grok:local-billing-log", weekly=5,
                        main_window_id="grok:weekly", windows=[
                            QuotaWindow("grok:weekly", "Grok", 5, resets_at=reset, window_minutes=10080)])
    db.record_quota(old)
    newest = QuotaSnapshot(provider="grok", source=old.source, weekly=13,
                           updated_at=now, main_window_id="grok:weekly", windows=[
                               QuotaWindow("grok:weekly", "Grok", 13, resets_at=reset, window_minutes=10080)])
    server = LocalApiServer(lambda: {"grok": ProviderState("grok", newest, True, "")}, port=0)
    server.start()
    try:
        data, connected = read_monitor_data(db, base_url=server.base_url)
        assert connected
        assert data.states["grok"].snapshot.weekly == 13
        assert data.states["grok"].snapshot.updated_at == now
        assert data.states["grok"].live
    finally:
        server.stop()
        db.close()


def test_widget_checks_the_collector_profile_on_every_request(tmp_path, monkeypatch):
    from claude_usage_manager import backend_client, local_api
    from claude_usage_manager.utils import data_dir, data_store_id

    (data_dir() / "backend-control.json").write_text('{"desired_running":true}', encoding="utf-8")
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "first account"))
    first = local_api.profile_api_path("")
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "other account"))
    assert local_api.profile_api_path("") != first
    calls = []
    server = LocalApiServer(lambda: {}, lambda: calls.append("refresh"), port=0,
                            health_details={"mode": "backend", "connections_schema": 1, "data_store": data_store_id()})
    server.start()
    db = Database(tmp_path / "widget.db")
    try:
        monkeypatch.setattr(backend_client, "DEFAULT_PORT", server.address[1])
        assert backend_client.existing_backend_available()
        assert backend_client.read_monitor_data(db, base_url=server.base_url)[1]
        assert backend_client.request_backend_refresh(base_url=server.base_url)
        monkeypatch.setattr(backend_client, "profile_api_path", lambda path: first + path)
        assert not backend_client.existing_backend_available()
        assert not backend_client.read_monitor_data(db, base_url=server.base_url)[1]
        assert not backend_client.request_backend_refresh(base_url=server.base_url)
        assert calls == ["refresh"]
    finally:
        server.stop()
        db.close()
