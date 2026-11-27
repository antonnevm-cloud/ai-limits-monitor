"""Codex и Grok не желтеют от короткого сбоя; Grok берет лимит у Grok CLI; подсказки точек."""
from __future__ import annotations

import datetime as dt
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import claude_usage_manager.grok_provider as grok
import claude_usage_manager.quota_monitor_model as model
from claude_usage_manager.database import Database
from claude_usage_manager.models import QuotaSnapshot, QuotaWindow
from claude_usage_manager.quota_monitor import SOURCE_NOTES, dot_tooltip
from claude_usage_manager.quota_monitor_model import ProviderState, refresh_states

NOW = dt.datetime(2026, 11, 12, 10, 0, tzinfo=dt.timezone.utc)


def config(percent=79.0, start=None, end=None):
    start = start or NOW - dt.timedelta(days=4)
    end = end or start + dt.timedelta(days=7)
    return {
        "config": {
            "creditUsagePercent": percent,
            "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY", "start": start.isoformat(), "end": end.isoformat()},
            "isUnifiedBillingUser": True,
        },
        "subscription_tier": "SuperGrok",
    }


def snapshot(provider, percent=10.0, source=None, updated_at=None):
    return QuotaSnapshot(
        provider=provider, source=source or f"test:{provider}", updated_at=updated_at or dt.datetime.now(dt.timezone.utc), weekly=percent,
        windows=[QuotaWindow(id=f"{provider}:w", title="w", used_percent=percent, resets_at=NOW + dt.timedelta(days=3), window_minutes=10080)],
        main_window_id=f"{provider}:w",
    )


@pytest.fixture(autouse=True)
def clean_state():
    model._LAST_LIVE.clear()
    model._LAST_REASON.clear()
    grok.reset_cli_cache()
    yield
    model._LAST_LIVE.clear()
    model._LAST_REASON.clear()
    grok.reset_cli_cache()


# ------------------------------ Grok CLI ----------------------------------- #
def test_grok_quota_comes_from_cli_billing_and_is_live(monkeypatch):
    monkeypatch.setattr(grok, "read_billing_from_cli", lambda: config(79.0))
    result = grok.fetch_grok_quota(now=NOW)
    assert result.has_data() and result.weekly == 79.0 and result.fallback_source == ""
    assert result.updated_at == NOW and result.source == grok.SOURCE
    assert result.window("grok:weekly").window_minutes == 10080


def test_grok_cli_answer_is_reused_for_a_few_minutes(monkeypatch):
    calls = []
    monkeypatch.setattr(grok, "read_billing_from_cli", lambda: calls.append(1) or config(50.0))
    grok.fetch_grok_quota(now=NOW)
    grok.fetch_grok_quota(now=NOW)
    assert len(calls) == 1
    grok.reset_cli_cache()
    grok.fetch_grok_quota(now=NOW)
    assert len(calls) == 2


def test_grok_incomplete_answer_is_retried_without_success_cache(monkeypatch, tmp_path):
    clock = [100.0]
    calls = []
    incomplete = config(40.0)
    del incomplete["config"]["creditUsagePercent"]
    answers = iter((incomplete, config(41.0)))
    monkeypatch.setattr(grok.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(grok, "read_billing_from_cli", lambda: calls.append(1) or next(answers))
    monkeypatch.setattr(grok, "log_path", lambda: tmp_path / "empty.jsonl")
    assert not grok.fetch_grok_quota(now=NOW).has_data()
    assert not grok.fetch_grok_quota(now=NOW).has_data()
    assert len(calls) == 1
    clock[0] += grok.CLI_INVALID_RETRY_SECONDS + 1
    assert grok.fetch_grok_quota(now=NOW).weekly == 41.0
    assert len(calls) == 2


def test_grok_incompatible_plan_does_not_retry_every_20_seconds(monkeypatch, tmp_path):
    clock = [100.0]
    calls = []
    invalid = config(40.0)
    invalid["config"]["isUnifiedBillingUser"] = False
    monkeypatch.setattr(grok.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(grok, "read_billing_from_cli", lambda: calls.append(1) or invalid)
    monkeypatch.setattr(grok, "log_path", lambda: tmp_path / "empty.jsonl")
    for _ in range(7):
        assert not grok.fetch_grok_quota(now=NOW).has_data()
        clock[0] += 20
    assert len(calls) == 1
    clock[0] += 20
    grok.fetch_grok_quota(now=NOW)
    assert len(calls) == 2


def test_grok_newer_log_wins_over_cached_cli_and_preserves_observation_time(monkeypatch, tmp_path):
    calls = []
    log = tmp_path / "unified.jsonl"
    monkeypatch.setattr(grok, "log_path", lambda: log)
    monkeypatch.setattr(grok, "read_billing_from_cli", lambda: calls.append(1) or config(20.0))
    first = grok.fetch_grok_quota(now=NOW)
    assert first.updated_at == NOW
    later = NOW + dt.timedelta(seconds=30)
    log.write_text(json.dumps({"ts": later.isoformat(), "msg": "billing: fetched credits config",
                               "ctx": {"config": config(27.0)["config"]}}), encoding="utf-8")
    result = grok.fetch_grok_quota(now=NOW + dt.timedelta(minutes=1))
    assert result.weekly == 27.0 and result.updated_at == later
    assert len(calls) == 1


def test_grok_cached_cli_does_not_advance_observation_time(monkeypatch, tmp_path):
    monkeypatch.setattr(grok, "log_path", lambda: tmp_path / "empty.jsonl")
    monkeypatch.setattr(grok, "read_billing_from_cli", lambda: config(20.0))
    first = grok.fetch_grok_quota(now=NOW)
    second = grok.fetch_grok_quota(now=NOW + dt.timedelta(minutes=1))
    assert first.updated_at == second.updated_at == NOW


def test_grok_cli_failure_falls_back_to_a_fresh_log_record_as_live(monkeypatch, tmp_path):
    def broken():
        raise RuntimeError("Grok CLI closed the response stream")
    monkeypatch.setattr(grok, "read_billing_from_cli", broken)
    log = tmp_path / "unified.jsonl"
    start = NOW - dt.timedelta(days=1)
    log.write_text(json.dumps({"ts": (NOW - dt.timedelta(minutes=3)).isoformat(), "msg": "billing: fetched credits config",
                               "ctx": {"config": config(60.0, start)["config"]}}), encoding="utf-8")
    monkeypatch.setattr(grok, "log_path", lambda: log)
    result = grok.fetch_grok_quota(now=NOW)
    assert result.weekly == 60.0 and result.fallback_source == ""


def test_grok_cli_failure_and_old_log_names_both_reasons(monkeypatch, tmp_path):
    def broken():
        raise TimeoutError("Grok CLI response timeout")
    monkeypatch.setattr(grok, "read_billing_from_cli", broken)
    log = tmp_path / "unified.jsonl"
    start = NOW - dt.timedelta(days=1)
    log.write_text(json.dumps({"ts": (NOW - dt.timedelta(hours=2)).isoformat(), "msg": "billing: fetched credits config",
                               "ctx": {"config": config(60.0, start)["config"]}}), encoding="utf-8")
    monkeypatch.setattr(grok, "log_path", lambda: log)
    result = grok.fetch_grok_quota(now=NOW)
    assert result.has_data()
    assert "устарела" in result.fallback_source and "Grok CLI не ответил: Grok CLI response timeout" in result.fallback_source


def test_grok_cli_failure_without_log_is_an_error_with_the_cli_reason(monkeypatch, tmp_path):
    def broken():
        raise RuntimeError("Grok CLI не найден")
    monkeypatch.setattr(grok, "read_billing_from_cli", broken)
    monkeypatch.setattr(grok, "log_path", lambda: tmp_path / "нет.jsonl")
    result = grok.fetch_grok_quota(now=NOW)
    assert not result.has_data() and result.error == "Grok CLI не найден"


def test_grok_cli_answer_in_unexpected_shape_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(grok, "read_billing_from_cli", lambda: {"config": {"creditUsagePercent": 150, "isUnifiedBillingUser": True}})
    monkeypatch.setattr(grok, "log_path", lambda: tmp_path / "нет.jsonl")
    assert not grok.fetch_grok_quota(now=NOW).has_data()


def test_explicit_log_path_never_starts_the_cli(monkeypatch, tmp_path):
    def forbidden():
        raise AssertionError("CLI не должен запускаться при явном пути журнала")
    monkeypatch.setattr(grok, "read_billing_from_cli", forbidden)
    assert not grok.fetch_grok_quota(tmp_path / "нет.jsonl", now=NOW).has_data()


def test_cli_conversation_sends_only_initialize_and_billing(monkeypatch):
    sent = []

    class FakeStdin:
        def write(self, text):
            sent.append(json.loads(text))

        def flush(self):
            pass

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.stdin = FakeStdin()
            self.stdout = iter([
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 2, "result": config(33.0)}) + "\n",
            ])
            self.args = args

        def poll(self):
            return 0

    monkeypatch.setattr(grok, "grok_cli", lambda: grok.Path("grok.exe"))
    monkeypatch.setattr(grok.subprocess, "Popen", FakeProcess)
    result = grok.read_billing_from_cli()
    assert result["config"]["creditUsagePercent"] == 33.0
    assert [m["method"] for m in sent] == ["initialize", "_x.ai/billing"]
    assert sent[1]["params"] == {}


def test_cli_error_response_becomes_a_runtime_error(monkeypatch):
    class FakeStdin:
        def write(self, text):
            pass

        def flush(self):
            pass

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.stdin = FakeStdin()
            self.stdout = iter([
                json.dumps({"id": 1, "result": {}}) + "\n",
                json.dumps({"id": 2, "error": {"code": -32602, "message": "Invalid params"}}) + "\n",
            ])

        def poll(self):
            return 0

    monkeypatch.setattr(grok, "grok_cli", lambda: grok.Path("grok.exe"))
    monkeypatch.setattr(grok.subprocess, "Popen", FakeProcess)
    with pytest.raises(RuntimeError, match="-32602"):
        grok.read_billing_from_cli()


# ------------------------- Терпимость к сбоям ------------------------------ #
def fetcher(script):
    """Возвращает снимки по сценарию: снимок или текст ошибки на каждый вызов Codex/Grok."""
    calls = {"codex": 0, "grok": 0, "antigravity": 0}

    def fetch(provider):
        if provider not in script:
            return QuotaSnapshot(provider=provider, source=f"test:{provider}", error="не опрашивается")
        step = script[provider][min(calls[provider], len(script[provider]) - 1)]
        calls[provider] += 1
        if isinstance(step, str):
            return QuotaSnapshot(provider=provider, source=f"test:{provider}", error=step)
        return step

    return fetch


def test_short_failure_keeps_codex_and_grok_live(tmp_path, monkeypatch):
    db = Database(tmp_path / "u.db")
    monkeypatch.setattr(model, "fetch_quota", fetcher({
        "codex": [snapshot("codex", 10), "Codex app-server response timeout", snapshot("codex", 11)],
        "grok": [snapshot("grok", 20), "Grok CLI response timeout", snapshot("grok", 21)],
    }))
    first = refresh_states(db, excluded_providers=("claude",))
    assert first["codex"].live and first["grok"].live
    second = refresh_states(db, excluded_providers=("claude",))
    assert second["codex"].live and second["grok"].live
    assert second["codex"].snapshot.weekly == 10 and "временно не отвечает" in second["codex"].error
    assert "Codex app-server response timeout" in second["codex"].error
    third = refresh_states(db, excluded_providers=("claude",))
    assert third["codex"].live and third["codex"].snapshot.weekly == 11 and third["codex"].error == ""
    db.close()


def test_long_failure_turns_codex_and_grok_into_cache(tmp_path, monkeypatch):
    db = Database(tmp_path / "u.db")
    monkeypatch.setattr(model, "fetch_quota", fetcher({
        "codex": [snapshot("codex", 10), "нет ответа"],
        "grok": [snapshot("grok", 20), "нет ответа"],
    }))
    refresh_states(db, excluded_providers=("claude",))
    stamp, kept = model._LAST_LIVE["codex"]
    model._LAST_LIVE["codex"] = (stamp - model.LIVE_FAILURE_GRACE - dt.timedelta(seconds=1), kept)
    states = refresh_states(db, excluded_providers=("claude",))
    assert not states["codex"].live and states["codex"].error == "нет ответа"
    assert states["grok"].live
    db.close()


def test_failure_at_start_without_previous_live_is_not_live(tmp_path, monkeypatch):
    db = Database(tmp_path / "u.db")
    monkeypatch.setattr(model, "fetch_quota", fetcher({"codex": ["нет ответа"], "grok": ["нет ответа"]}))
    states = refresh_states(db, excluded_providers=("claude",))
    assert not states["codex"].live and not states["grok"].live
    db.close()


def test_stale_fallback_of_grok_inside_grace_keeps_the_last_live_value(tmp_path, monkeypatch):
    db = Database(tmp_path / "u.db")
    stale = snapshot("grok", 5)
    stale.fallback_source = "Запись журнала Grok устарела"
    monkeypatch.setattr(model, "fetch_quota", fetcher({"grok": [snapshot("grok", 20), stale]}))
    refresh_states(db, excluded_providers=("claude",))
    states = refresh_states(db, excluded_providers=("claude",))
    assert states["grok"].live and states["grok"].snapshot.weekly == 20
    db.close()


def test_antigravity_is_not_covered_by_the_grace(tmp_path, monkeypatch):
    db = Database(tmp_path / "u.db")
    monkeypatch.setattr(model, "fetch_quota", fetcher({"antigravity": [snapshot("antigravity"), "нет ответа"]}))
    assert refresh_states(db, excluded_providers=("claude",))["antigravity"].live
    assert not refresh_states(db, excluded_providers=("claude",))["antigravity"].live
    db.close()


def test_same_failure_is_logged_once(tmp_path, monkeypatch, caplog):
    db = Database(tmp_path / "u.db")
    monkeypatch.setattr(model, "fetch_quota", fetcher({"codex": ["нет ответа"]}))
    with caplog.at_level("WARNING"):
        refresh_states(db, excluded_providers=("claude",))
        refresh_states(db, excluded_providers=("claude",))
    assert sum("Источник codex без live-данных" in record.getMessage() for record in caplog.records) == 1
    db.close()


# ------------------------------ Подсказки ---------------------------------- #
def state(provider, snap, live=False, error=""):
    return ProviderState(provider, snap, live, error)


def test_dot_tooltips_name_the_right_agent_and_source():
    grok_stale = snapshot("grok")
    grok_stale.fallback_source = "Запись журнала Grok устарела (от 12.11 10:25)"
    text = dot_tooltip("grok", "локальные данные", state("grok", grok_stale))
    assert "Grok" in text and "Claude Code" not in text and "устарела" in text and SOURCE_NOTES["grok"] in text
    codex = dot_tooltip("codex", "кэш", state("codex", snapshot("codex"), error="Codex app-server response timeout"))
    assert "Codex" in codex and "Codex app-server response timeout" in codex and "Claude" not in codex
    online = dot_tooltip("codex", "онлайн", state("codex", snapshot("codex"), True, "Источник временно не отвечает (x)"))
    assert online.startswith("ОНЛАЙН") and "временно не отвечает" in online
    assert "Claude Code" in dot_tooltip("claude", "локальные данные", state("claude", snapshot("claude")))
    assert dot_tooltip("antigravity", "нет данных", state("antigravity", QuotaSnapshot(provider="antigravity"))).startswith("НЕТ ДАННЫХ")


def test_fallback_status_is_not_claude_specific_for_other_agents(tmp_path, monkeypatch):
    db = Database(tmp_path / "u.db")
    stale = snapshot("grok", 5)
    stale.fallback_source = "Запись журнала Grok устарела"
    monkeypatch.setattr(model, "fetch_quota", fetcher({"grok": [stale]}))
    states = refresh_states(db, excluded_providers=("claude",))
    assert not states["grok"].live and "Claude" not in states["grok"].error
    db.close()


def test_grace_counts_from_the_age_of_the_data_not_from_the_poll(tmp_path, monkeypatch):
    """Свежая запись журнала Grok (14 минут назад) не должна давать еще 15 минут сверху."""
    db = Database(tmp_path / "u.db")
    old = snapshot("grok", 20, updated_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=14))
    monkeypatch.setattr(model, "fetch_quota", fetcher({"grok": [old, "нет ответа"]}))
    refresh_states(db, excluded_providers=("claude",))
    assert refresh_states(db, excluded_providers=("claude",))["grok"].live
    stamp, kept = model._LAST_LIVE["grok"]
    assert stamp == old.updated_at
    model._LAST_LIVE["grok"] = (stamp - dt.timedelta(minutes=2), kept)
    assert not refresh_states(db, excluded_providers=("claude",))["grok"].live
    db.close()


def test_failed_grok_cli_is_not_started_again_within_two_minutes(monkeypatch):
    calls = []

    def broken():
        calls.append(1)
        raise TimeoutError("Grok CLI response timeout")

    monkeypatch.setattr(grok, "read_billing_from_cli", broken)
    for _ in range(3):
        result = grok.fetch_grok_quota_from_cli(NOW)
        assert not result.has_data() and "timeout" in result.error
    assert len(calls) == 1
    grok.reset_cli_cache()
    grok.fetch_grok_quota_from_cli(NOW)
    assert len(calls) == 2


def test_codex_quota_is_retried_once_in_the_same_cycle(monkeypatch):
    from claude_usage_manager import quota
    answers = [QuotaSnapshot(provider="codex", source="x", error="timeout"), snapshot("codex", 12)]
    calls = []
    monkeypatch.setattr(quota, "fetch_codex_quota_from_app_server", lambda: calls.append(1) or answers[len(calls) - 1])
    assert quota.fetch_quota("codex").has_data() and len(calls) == 2


def test_cli_process_is_terminated_and_killed_when_it_will_not_stop(monkeypatch):
    events = []

    class FakeStdin:
        def write(self, text):
            pass

        def flush(self):
            pass

    class StubbornProcess:
        def __init__(self, *args, **kwargs):
            self.stdin = FakeStdin()
            self.stdout = iter([
                json.dumps({"id": 1, "result": {}}) + "\n",
                json.dumps({"id": 2, "result": config(33.0)}) + "\n",
            ])

        def poll(self):
            return None

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            events.append("wait")
            raise grok.subprocess.TimeoutExpired("grok", timeout)

        def kill(self):
            events.append("kill")

    monkeypatch.setattr(grok, "grok_cli", lambda: grok.Path("grok.exe"))
    monkeypatch.setattr(grok.subprocess, "Popen", StubbornProcess)
    assert grok.read_billing_from_cli()["config"]["creditUsagePercent"] == 33.0
    assert events == ["terminate", "wait", "kill"]


def test_api_marks_a_kept_live_provider_as_degraded_without_the_reason():
    from claude_usage_manager.local_api import provider_payload
    kept = ProviderState("codex", snapshot("codex"), True, "Источник временно не отвечает (secret reason)")
    payload = provider_payload("codex", kept, NOW)
    assert payload["source_state"] == "live" and payload["degraded"] is True
    assert "secret reason" not in json.dumps(payload, ensure_ascii=False)
    assert provider_payload("codex", ProviderState("codex", snapshot("codex"), True, ""), NOW)["degraded"] is False
    assert provider_payload("codex", ProviderState("codex", snapshot("codex"), False, "x"), NOW)["degraded"] is False
