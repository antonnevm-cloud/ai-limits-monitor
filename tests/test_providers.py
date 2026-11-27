import datetime as dt
import json
import os
import sqlite3
from pathlib import Path

import pytest

from claude_usage_manager.models import Period, ThreadRow
from claude_usage_manager.process_utils import hidden_process_options
from claude_usage_manager.providers import (
    _BaseProvider,
    _codex_user_text,
    _parse_codex_file,
)

UTC = dt.timezone.utc
PERIOD = Period(
    "test",
    "Test",
    dt.datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
    dt.datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
)


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def test_codex_user_text_skips_generated_and_reads_input_text():
    payload = {
        "replacement_history": [
            {"role": "user", "content": [{"type": "input_text", "text": "<recommended_plugins>служебное</recommended_plugins>"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "Проведи полный аудит конфигурации и подготовь исправления"}]},
        ]
    }
    assert _codex_user_text(payload).startswith("Проведи полный аудит")


def test_parse_codex_file_exact_period(tmp_path):
    path = tmp_path / "rollout.jsonl"
    values = [
        {
            "timestamp": "2026-08-05T09:00:00Z",
            "payload": {"session_id": "S1", "cwd": r"C:\\Workspace\\Demo"},
        },
        {
            "timestamp": "2026-08-05T09:30:00Z",
            "payload": {"role": "user", "content": [{"type": "input_text", "text": "Сделай проверку проекта Demo"}]},
        },
        {
            "timestamp": "2026-08-05T10:30:00Z",
            "payload": {
                "model": "gpt-5.6-terra",
                "turn_id": "T1",
                "info": {"last_token_usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 600,
                    "cache_write_input_tokens": 100,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 10,
                    "total_tokens": 1050,
                }},
            },
        },
        {
            "timestamp": "2026-08-05T12:30:00Z",
            "payload": {
                "model": "gpt-5.6-terra",
                "turn_id": "OUTSIDE",
                "info": {"last_token_usage": {"input_tokens": 9999, "output_tokens": 1, "total_tokens": 10000}},
            },
        },
    ]
    _write_jsonl(path, values)
    result = _parse_codex_file(path, PERIOD)
    assert result is not None
    assert result["internal_id"] == "S1"
    assert result["cwd"] == r"C:\\Workspace\\Demo"
    assert result["title"] == "Сделай проверку проекта Demo"
    assert result["total_tokens"] == 1050
    assert result["input"] == 300
    assert result["cache_read"] == 600
    assert result["cache_creation"] == 100
    assert result["output"] == 50
    assert result["models"] == {"gpt-5.6-terra"}
    assert len(result["events"]) == 1




def test_finalize_filters_hidden_members_before_grouping():
    class RulesDb:
        def thread_rules(self):
            return {
                "visible": {"hidden": False, "pinned": False, "group_name": "Задача"},
                "hidden": {"hidden": True, "pinned": False, "group_name": "Задача"},
            }

    provider = _BaseProvider(RulesDb())
    provider.agent = "claude"
    result = provider._finalize(
        PERIOD,
        [
            ThreadRow("visible", "Видимый", "demo", total_tokens=10, member_ids=["visible"]),
            ThreadRow("hidden", "Скрытый", "demo", total_tokens=90, member_ids=["hidden"]),
        ],
        [],
        "",
        False,
        False,
        [],
    )

    assert result.work_threads == 1
    assert result.total_tokens == 10
    assert len(result.threads) == 1
    assert result.threads[0].member_ids == ["visible"]


@pytest.mark.skipif(os.name != "nt", reason="Windows-only process flags")
def test_hidden_process_options_prevent_console_window():
    import subprocess

    options = hidden_process_options()
    assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    startup = options["startupinfo"]
    assert startup.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert startup.wShowWindow == subprocess.SW_HIDE


def test_codex_reads_archived_and_active_rollouts_once_and_uses_profile_names(tmp_path, monkeypatch):
    from claude_usage_manager import provider_settings
    from claude_usage_manager.providers import MultiAgentAnalyzer

    profile = tmp_path / "profile #2"
    monkeypatch.setattr(provider_settings, "provider_profile", lambda _: profile)
    active, archive = profile / "sessions" / "2026" / "01" / "01", profile / "archived_sessions"
    active.mkdir(parents=True)
    archive.mkdir()
    base = {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 10, "total_tokens": 1010}
    modern = {"type": "token_usage_record", "timestamp": "2026-08-05T11:00:00Z",
              "payload": {"response_id": "r1", "usage": base}}
    legacy = {"type": "event_msg", "timestamp": "2026-08-05T11:00:01Z",
              "payload": {"type": "token_count", "info": {"last_token_usage": base, "total_token_usage": base}}}
    context = {"type": "turn_context", "payload": {"model": "gpt-6.1-sol", "turn_id": "t1"}}
    head = {"type": "session_meta", "payload": {"id": "a1", "cwd": "/Users/test/Example Project"}}
    _write_jsonl(active / "rollout-a1.jsonl", [head, context, modern, legacy])
    _write_jsonl(archive / "rollout-a1.jsonl", [head, context, modern, legacy])
    _write_jsonl(archive / "rollout-a2.jsonl", [
        {"type": "session_meta", "payload": {"id": "a2", "cwd": "/Users/test/Example Project"}}, context, legacy,
        dict(legacy, timestamp="2026-08-05T11:03:01Z"),
    ])
    _write_jsonl(profile / "session_index.jsonl", [
        {"id": "a1", "thread_name": "Старое имя"}, {"id": "a1", "thread_name": "Новое имя"},
    ])
    connection = sqlite3.connect(profile / "state_5.sqlite")
    connection.execute("CREATE TABLE threads(id TEXT, title TEXT, archived INTEGER)")
    connection.execute("INSERT INTO threads VALUES('a2', 'Из архива', 1)")
    connection.commit()
    connection.close()
    result = MultiAgentAnalyzer().analyze("codex", PERIOD, collect_quota=False)
    assert {row.title for row in result.threads} == {"Новое имя", "Из архива"}
    assert {row.project for row in result.threads} == {"Example Project"}
    assert result.total_tokens == result.model_rows[0].total_tokens == 2020
    assert result.total_cost == pytest.approx(2 * (400 * 2 + 600 * .10 + 10 * 10) / 1e6)
    assert sum(bucket.total_tokens for bucket in result.timeline) == 2020


def test_disabled_providers_never_open_a_profile_or_fetch_quota(monkeypatch):
    from claude_usage_manager import provider_settings
    from claude_usage_manager.cache_threads import ThreadScanner
    from claude_usage_manager.providers import AGENTS, MultiAgentAnalyzer
    from claude_usage_manager.thread_usage import UsageScanner

    monkeypatch.setattr(provider_settings, "provider_enabled", lambda _: False)
    monkeypatch.setattr(provider_settings, "provider_profile", lambda *_: pytest.fail("Чтение отключенного профиля"))
    analyzer = MultiAgentAnalyzer()
    for provider, _ in AGENTS:
        result = analyzer.analyze(provider, PERIOD)
        assert result.threads == [] and "отключен" in result.warnings[0]
    assert all(not rows for rows in ThreadScanner().scan().values())
    assert UsageScanner().scan({}, {}).threads == {}
