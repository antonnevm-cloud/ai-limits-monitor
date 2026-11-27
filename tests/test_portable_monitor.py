import datetime as dt
import io
import json
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

from claude_usage_manager import backend, backend_client, claude_local
from claude_usage_manager.database import Database
from claude_usage_manager.models import QuotaSnapshot, QuotaWindow
from claude_usage_manager.provider_settings import load_connections, save_connections
from claude_usage_manager.quota_monitor_model import PROVIDERS, ProviderState, cached_snapshot
from claude_usage_manager.utils import data_dir, data_store_id


def test_installed_widget_reuses_only_compatible_backend(monkeypatch):
    (data_dir() / "backend-control.json").write_text('{"desired_running":true}', encoding="utf-8")
    health = {"mode": "backend", "schema_version": 1, "status": "ok", "service": "ai-limits-monitor"}
    monkeypatch.setattr(backend_client.urllib.request, "urlopen",
                        lambda *a, **kw: io.BytesIO(json.dumps(health).encode()))
    assert backend_client.existing_backend_available()
    save_connections(load_connections())
    assert not backend_client.existing_backend_available()
    health.update(connections_schema=1, data_store=data_store_id())
    assert backend_client.existing_backend_available()
    health["data_store"] = "another-profile"
    assert not backend_client.existing_backend_available()


def test_history_follows_the_selected_provider_profile(tmp_path):
    settings = load_connections()
    db = Database(tmp_path / "monitor.sqlite")
    try:
        snapshot = QuotaSnapshot(provider="codex", source="codex-app-server:account/rateLimits/read", weekly=10)
        db.record_quota(snapshot)
        custom = dict(settings, codex=replace(settings["codex"], profile=str(tmp_path / "second")))
        save_connections(custom)
        assert not cached_snapshot(db, "codex").has_data()
        db.record_quota(replace(snapshot, weekly=80))
        assert [r["weekly"] for r in db.quota_samples(provider="codex")] == [80]
        save_connections(settings)
        assert [r["weekly"] for r in db.quota_samples(provider="codex")] == [10]
    finally:
        db.close()


def test_widget_collector_works_without_any_http_listener(monkeypatch, tmp_path):
    seen = {name: threading.Event() for name in PROVIDERS}
    def refresh(db, provider):
        seen[provider].set()
        return ProviderState(provider, QuotaSnapshot(provider=provider, weekly=17), True, "")
    def no_listener(*args, **kwargs):
        raise AssertionError("Автономный виджет не должен открывать сетевой порт")
    monkeypatch.setattr(backend, "LocalApiServer", no_listener)
    service = backend.BackendService(serve_http=False, db_factory=lambda: Database(tmp_path / "widget.db"),
                                     provider_refresh=refresh)
    try:
        service.start()
        assert all(event.wait(4) for event in seen.values())
    finally:
        service.stop()
    assert {s.snapshot.weekly for s in service.states().values()} == {17}


def test_statusline_keeps_the_profile_that_installed_it(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LIMITS_DATA_DIR", str(tmp_path / "viewer"))
    settings = load_connections()
    first = tmp_path / "first"
    second = tmp_path / "second"
    save_connections(dict(settings, claude=replace(settings["claude"], profile=str(second))))
    key = claude_local._profile_key(first)
    target = tmp_path / "viewer"
    root = Path(__file__).resolve().parents[1]
    payload = {"rate_limits": {"seven_day": {"used_percentage": 27}}, "email": "ignored@example.invalid"}
    result = subprocess.run([sys.executable, str(root / "monitor.py"), "--claude-statusline", key, str(target)],
                            input=json.dumps(payload), text=True, capture_output=True, check=True, timeout=15)
    assert "7d: 27%" in result.stdout
    assert not (target / "profiles" / claude_local._profile_key(second) / claude_local.DATA_FILE).exists()
    saved = (target / "profiles" / key / claude_local.DATA_FILE).read_text(encoding="utf-8")
    assert "ignored@example.invalid" not in saved


def test_antigravity_cache_expires_at_the_end_of_the_measured_window(tmp_path):
    db = Database(tmp_path / "quota.sqlite")
    try:
        ended = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
        db.record_quota(QuotaSnapshot(provider="antigravity", source="antigravity:cli-usage", weekly=12,
                                      windows=[QuotaWindow("ag-group:gemini:weekly", "Gemini", 12,
                                                           ended, 10080, "group")]))
        assert not cached_snapshot(db, "antigravity").has_data()
    finally:
        db.close()
