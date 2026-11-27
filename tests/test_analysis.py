import datetime as dt
import os

import pytest

from claude_usage_manager.analysis import (
    Analyzer,
    _collect_event,
    aggregate_models,
    aggregate_projects,
    apply_grouping,
    build_timeline,
    build_timeline_session_costs,
    merge_sessions,
    resolve_title,
    scan_logs,
)
from claude_usage_manager.models import Period, ThreadRow

UTC = dt.timezone.utc
START = dt.datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
END = dt.datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _assistant(message_id, iso, tokens=100, model="claude-sonnet-5"):
    return {
        "type": "assistant",
        "sessionId": "S1",
        "timestamp": iso,
        "message": {
            "id": message_id,
            "model": model,
            "usage": {
                "input_tokens": tokens,
                "output_tokens": tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }


def test_scan_range_and_dedup(make_claude_root, tmp_path):
    records = [
        {"type": "user", "sessionId": "S1", "cwd": "C:\\Workspace\\Demo",
         "timestamp": "2026-08-05T10:05:00Z",
         "message": {"content": "Проанализируй расход за неделю подробно"}},
        _assistant("m1", "2026-08-05T10:10:00Z"),
        _assistant("m1", "2026-08-05T10:20:00Z"),          # дубль message.id
        _assistant("m2", "2026-08-05T11:00:00Z"),
        _assistant("synthetic-abc", "2026-08-05T11:05:00Z"),  # synthetic -> пропуск
        _assistant("m3", "2026-08-05T13:00:00Z"),          # вне диапазона
    ]
    make_claude_root("Demo", "S1.jsonl", records)

    metadata, events = scan_logs(make_claude_root.root, START, END)

    assert len(events) == 2  # m1 (один раз) + m2, synthetic и вне диапазона исключены
    assert metadata["S1"]["period_prompt"].startswith("Проанализируй")
    # Журналы никуда не копируются: временного каталога рядом нет.
    assert not list(tmp_path.glob("cum-*"))


def test_scan_exact_boundaries(make_claude_root, tmp_path):
    records = [
        _assistant("edge_start", "2026-08-05T10:00:00Z"),   # ровно начало — включ.
        _assistant("edge_end", "2026-08-05T12:00:00Z"),     # ровно конец — включ.
        _assistant("before", "2026-08-05T09:59:59Z"),       # до — исключ.
        _assistant("after", "2026-08-05T12:00:01Z"),        # после — исключ.
    ]
    make_claude_root("Demo", "S1.jsonl", records)
    _metadata, events = scan_logs(make_claude_root.root, START, END)
    assert len(events) == 2


def test_arbitrary_project_folder_and_user_request_reach_the_report(make_claude_root):
    prompt = "Read GitHub issue 41 and explain the cause"
    project = r"F:\Team\Clients\Network\v2"
    make_claude_root("arbitrary-project", "S1.jsonl", [
        {"type": "user", "sessionId": "S1", "cwd": project, "timestamp": START.isoformat(),
         "message": {"content": prompt}},
        _assistant("m1", START.isoformat()),
    ])
    metadata, _ = scan_logs(make_claude_root.root, START, END)
    rows = merge_sessions([{"sessionId": "S1", "projectPath": "arbitrary-project"}], metadata, {})
    assert rows[0].title == prompt
    assert rows[0].project == "v2"


def test_subagent_without_parent_in_period_belongs_to_parent_project():
    parent = "22222222-2222-2222-2222-222222222222"
    metadata = {parent: {"custom_titles": [], "first_prompt": None, "period_prompt": None,
                         "cwd": "/Users/test/Work/Project"}}
    rows = merge_sessions([{"sessionId": "agent-1", "projectPath": f"Project/{parent}/subagents",
                            "totalCost": 1.0, "totalTokens": 10}], metadata, {})
    # Расход не уходит в несуществующий проект "subagents" (analytics#8).
    assert [row.project for row in rows] == ["Project"]
    assert rows[0].project_path == "Project"


def test_resolve_title_priority():
    info = {"custom_titles": ["Мой заголовок"], "first_prompt": "первая команда пользователя",
            "period_prompt": None}
    # customTitle приоритетнее prompt.
    title, service = resolve_title("S1", "C:\\Workspace\\Demo", info, {})
    assert title == "Мой заголовок" and service is False
    # title_override приоритетнее всего.
    title2, _ = resolve_title("S1", "C:\\Workspace\\Demo", info,
                              {"S1": {"title_override": "Ручное имя"}})
    assert title2 == "Ручное имя"
    # fallback на prompt при отсутствии customTitle.
    title3, _ = resolve_title("S2", "C:\\Workspace\\Demo",
                              {"custom_titles": [], "first_prompt": "сделай отчёт по продажам"}, {})
    assert title3.startswith("сделай отчёт")


def test_project_folder_keeps_the_session_title():
    info = {"custom_titles": ["Тред с планом"], "first_prompt": "Проверь задачу 42"}
    title, service = resolve_title(
        "S1", "C:\\Workspace\\example-attach-session", info, {})
    assert title == "Тред с планом" and service is False


def test_subagent_merge():
    parent_id = "11111111-1111-1111-1111-111111111111"
    raw = [
        {"sessionId": parent_id, "projectPath": "C:\\Workspace\\Demo", "totalCost": 1.0,
         "totalTokens": 1000, "modelsUsed": ["claude-opus-4-8"],
         "firstActivity": "2026-08-05T10:00:00Z", "lastActivity": "2026-08-05T10:30:00Z"},
        {"sessionId": "child", "projectPath": f"C:\\Workspace\\Demo\\subagents\\{parent_id}",
         "totalCost": 0.5, "totalTokens": 500, "modelsUsed": ["claude-sonnet-5"],
         "firstActivity": "2026-08-05T10:05:00Z", "lastActivity": "2026-08-05T10:40:00Z"},
    ]
    rows = merge_sessions(raw, {}, {})
    assert len(rows) == 1
    row = rows[0]
    assert row.subagents == 1
    assert row.cost == 1.5 and row.total_tokens == 1500
    assert set(row.models) == {"claude-opus-4-8", "claude-sonnet-5"}
    assert row.last_activity.isoformat().startswith("2026-08-05T10:40")
    assert "child" in row.member_ids and parent_id in row.member_ids


def test_apply_grouping():
    rows = [
        ThreadRow(session_id="A", title="A", project="P1", cost=2.0, total_tokens=200,
                  models=["claude-sonnet-5"], group_name="Спринт"),
        ThreadRow(session_id="B", title="B", project="P1", cost=3.0, total_tokens=300,
                  models=["claude-opus-4-8"], group_name="Спринт"),
        ThreadRow(session_id="C", title="C", project="P2", cost=1.0, total_tokens=100),
    ]
    grouped = apply_grouping(rows)
    groups = [r for r in grouped if r.is_group]
    assert len(grouped) == 2  # группа + одиночный C
    assert len(groups) == 1
    g = groups[0]
    assert g.title == "Спринт" and g.cost == 5.0 and g.total_tokens == 500
    assert set(g.member_ids) == {"A", "B"}


def test_analyzer_filters_hidden_group_members_before_grouping(monkeypatch):
    import claude_usage_manager.analysis as analysis_module

    class RulesDb:
        def thread_rules(self):
            return {
                "visible": {"hidden": False, "pinned": False, "group_name": "Задача"},
                "hidden": {"hidden": True, "pinned": False, "group_name": "Задача"},
            }

    monkeypatch.setattr(analysis_module, "scan_logs", lambda *_args: ({}, []))
    monkeypatch.setattr(
        analysis_module,
        "local_sessions",
        lambda _events: [
            {"sessionId": "visible", "projectPath": "C--Workspace-Demo", "totalCost": 1.0,
             "totalTokens": 10, "modelsUsed": ["claude-sonnet-5"]},
            {"sessionId": "hidden", "projectPath": "C--Workspace-Demo", "totalCost": 9.0,
             "totalTokens": 90, "modelsUsed": ["claude-sonnet-5"]},
        ],
    )
    monkeypatch.setattr(analysis_module, "weekly_consumed", lambda *_args, **_kwargs: None)

    result = Analyzer(RulesDb()).analyze(
        Period("test", "Тест", START.astimezone(), END.astimezone()),
        collect_quota=False,
    )

    assert result.total_tokens == 10
    assert result.work_threads == 1
    assert len(result.threads) == 1
    assert result.threads[0].member_ids == ["visible"]


def test_aggregate_projects():
    rows = [
        ThreadRow(session_id="A", title="A", project="P1", cost=2.0, total_tokens=200),
        ThreadRow(session_id="B", title="B", project="P1", cost=3.0, total_tokens=300),
        ThreadRow(session_id="C", title="C", project="P2", cost=5.0, total_tokens=500),
    ]
    projects = aggregate_projects(rows, total_cost=10.0)
    by_name = {p.project: p for p in projects}
    assert by_name["P1"].threads == 2 and by_name["P1"].cost == 5.0
    assert abs(by_name["P2"].share - 50.0) < 1e-6


def test_aggregate_models_visible_only():
    raw = [
        {"sessionId": "S1", "modelBreakdowns": [
            {"modelName": "claude-opus-4-8", "cost": 4.0, "inputTokens": 10,
             "outputTokens": 20, "cacheCreationTokens": 0, "cacheReadTokens": 0}]},
        {"sessionId": "S2", "modelBreakdowns": [
            {"modelName": "claude-sonnet-5", "cost": 1.0, "inputTokens": 5,
             "outputTokens": 5, "cacheCreationTokens": 0, "cacheReadTokens": 0}]},
    ]
    models = aggregate_models(raw, visible_session_ids={"S1"})
    assert len(models) == 1
    assert models[0].model == "claude-opus-4-8" and models[0].cost == 4.0
    assert models[0].total_tokens == 30


def test_build_timeline_hourly():
    period = Period("today", "Сегодня", START.astimezone(), END.astimezone())
    events = [
        {"session_id": "S1", "timestamp": dt.datetime(2026, 8, 5, 10, 15, tzinfo=UTC),
         "model": "claude-sonnet-5", "tokens": 100},
        {"session_id": "S1", "timestamp": dt.datetime(2026, 8, 5, 10, 45, tzinfo=UTC),
         "model": "claude-sonnet-5", "tokens": 300},
        {"session_id": "S1", "timestamp": dt.datetime(2026, 8, 5, 11, 5, tzinfo=UTC),
         "model": "claude-sonnet-5", "tokens": 100},
    ]
    buckets = build_timeline(period, events, {"S1": 5.0}, {"S1"})
    total_tokens = sum(b.total_tokens for b in buckets)
    total_cost = sum(b.estimated_cost for b in buckets)
    assert total_tokens == 500
    assert abs(total_cost - 5.0) < 1e-6  # вся стоимость распределена


def test_analyzer_empty_period_returns_empty_report(make_claude_root, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(make_claude_root.root))
    period = Period("empty", "Пустой период", START.astimezone(), END.astimezone())
    result = Analyzer().analyze(period, collect_quota=False)
    assert result.threads == []
    assert result.total_tokens == 0
    assert result.total_cost == 0.0
    assert any("не найдено" in warning for warning in result.warnings)


def test_only_an_explicit_rule_hides_a_session():
    raw = [{
        "sessionId": "SVC",
        "projectPath": r"C:\Workspace\example-attach-session",
        "totalCost": 1.0,
        "totalTokens": 100,
        "modelsUsed": ["claude-opus-4-8"],
    }]
    metadata = {"SVC": {"custom_titles": [], "first_prompt": "Проверь задачу 42"}}
    default_row = merge_sessions(raw, metadata, {})[0]
    hidden_row = merge_sessions(raw, metadata, {"SVC": {"hidden": True}})[0]
    renamed = merge_sessions(raw, metadata, {"SVC": {"title_override": "Тред по задаче", "hidden": False}})[0]
    assert default_row.title == "Проверь задачу 42" and not default_row.is_service and not default_row.hidden
    assert hidden_row.hidden
    assert renamed.title == "Тред по задаче" and not renamed.hidden


def test_collect_event_uses_nested_cache_creation():
    events = []
    accepted = _collect_event(
        events, set(), "S1", START,
        {
            "id": "m-nested",
            "model": "claude-opus-5",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 7,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 13,
                "cache_creation": {
                    "ephemeral_1h_input_tokens": 18,
                    "ephemeral_5m_input_tokens": 2,
                },
            },
        },
    )
    assert accepted is True
    assert events[0]["tokens"] == 41  # 1 + 7 + (18 + 2) + 13


def test_duplicate_session_id_rows_are_aggregated():
    raw = [
        {
            "sessionId": "SAME", "projectPath": "C--Workspace-sample-project",
            "totalCost": 10.0, "totalTokens": 1000, "inputTokens": 10,
            "modelsUsed": ["claude-sonnet-5"],
            "firstActivity": "2026-08-05T10:00:00Z",
            "lastActivity": "2026-08-05T10:20:00Z",
        },
        {
            "sessionId": "SAME",
            "projectPath": "C--Workspace-sample-project--claude-worktrees-test",
            "totalCost": 2.0, "totalTokens": 200, "outputTokens": 20,
            "modelsUsed": ["claude-opus-5"],
            "firstActivity": "2026-08-05T09:50:00Z",
            "lastActivity": "2026-08-05T10:30:00Z",
        },
    ]
    rows = merge_sessions(raw, {}, {})
    assert len(rows) == 1
    row = rows[0]
    assert row.cost == 12.0 and row.total_tokens == 1200
    assert row.input_tokens == 10 and row.output_tokens == 20
    assert set(row.models) == {"claude-sonnet-5", "claude-opus-5"}
    assert "worktrees" not in row.project_path.casefold()
    assert row.first_activity.isoformat().startswith("2026-08-05T09:50")
    assert row.last_activity.isoformat().startswith("2026-08-05T10:30")


def test_short_session_gets_project_fallback_title():
    raw = [{
        "sessionId": "33333333-3333-4333-8333-333333333333",
        "projectPath": "C--Workspace-examples-tools-sandbox-c",
        "totalCost": 0.1,
        "totalTokens": 10,
    }]
    rows = merge_sessions(raw, {}, {})
    assert len(rows) == 1
    assert not rows[0].title.startswith("Тред ")
    assert "sandbox-c" in rows[0].title


def test_timeline_costs_attach_workflow_to_parent():
    parent = "11111111-1111-1111-1111-111111111111"
    raw = [
        {"sessionId": parent, "projectPath": "C--Workspace-Demo", "totalCost": 3.0},
        {
            "sessionId": "wf-child",
            "projectPath": f"C--Workspace-Demo\\{parent}\\subagents\\workflows",
            "totalCost": 2.0,
        },
    ]
    events = [{"session_id": parent, "timestamp": START, "tokens": 100, "model": "m"}]
    costs = build_timeline_session_costs(raw, events)
    assert costs[parent] == 5.0


def test_search_preserves_period_totals_and_share(make_claude_root, monkeypatch):
    import claude_usage_manager.analysis as analysis_module

    records_a = [
        {"type": "custom-title", "sessionId": "A", "customTitle": "Alpha task"},
        _assistant("a1", "2026-08-05T10:10:00Z"),
    ]
    records_a[-1]["sessionId"] = "A"
    records_b = [
        {"type": "custom-title", "sessionId": "B", "customTitle": "Beta task"},
        _assistant("b1", "2026-08-05T10:20:00Z"),
    ]
    records_b[-1]["sessionId"] = "B"
    make_claude_root("Demo", "A.jsonl", records_a)
    make_claude_root("Demo", "B.jsonl", records_b)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(make_claude_root.root))
    monkeypatch.setattr(
        analysis_module, "local_sessions",
        lambda _events: [
            {
                "sessionId": "A", "projectPath": "C--Workspace-Demo",
                "totalCost": 6.0, "totalTokens": 200,
                "modelsUsed": ["claude-sonnet-5"],
                "modelBreakdowns": [{
                    "modelName": "claude-sonnet-5", "cost": 6.0,
                    "inputTokens": 100, "outputTokens": 100,
                    "cacheCreationTokens": 0, "cacheReadTokens": 0,
                }],
            },
            {
                "sessionId": "B", "projectPath": "C--Workspace-Demo",
                "totalCost": 4.0, "totalTokens": 200,
                "modelsUsed": ["claude-sonnet-5"],
                "modelBreakdowns": [{
                    "modelName": "claude-sonnet-5", "cost": 4.0,
                    "inputTokens": 100, "outputTokens": 100,
                    "cacheCreationTokens": 0, "cacheReadTokens": 0,
                }],
            },
        ],
    )
    monkeypatch.setattr(analysis_module, "weekly_consumed", lambda *_args, **_kwargs: 20.0)
    period = Period("test", "Test", START.astimezone(), END.astimezone())
    result = Analyzer().analyze(period, search="Alpha", collect_quota=False)
    assert result.total_cost == 10.0
    assert result.total_tokens == 400
    assert result.work_threads == 2
    assert len(result.threads) == 1
    assert result.threads[0].title == "Alpha task"
    assert abs(result.threads[0].share - 60.0) < 1e-9
    assert abs(result.threads[0].weekly_pp - 12.0) < 1e-9
    assert result.meta["displayed_thread_count"] == 1
    assert result.meta["scope_thread_count"] == 2


def test_standalone_claude_report_uses_profile_prices_titles_and_subagents(make_claude_root, monkeypatch):
    import pytest
    from claude_usage_manager import analysis, process_utils, provider_settings

    monkeypatch.setattr(process_utils.subprocess, "Popen", lambda *_a, **_k: pytest.fail("Внешний процесс"))
    monkeypatch.setattr(provider_settings, "provider_profile", lambda _: make_claude_root.root)
    parent = "11111111-1111-1111-1111-111111111111"
    event = _assistant("m1", START.isoformat(), 0, "claude-opus-5-5")
    event.update(sessionId=parent, cwd="/Users/test/Work/Project")
    event["message"]["usage"] = {"input_tokens": 20, "output_tokens": 10, "cache_read_input_tokens": 3,
                                 "cache_creation": {"ephemeral_1h_input_tokens": 5}}
    make_claude_root("Project", parent + ".jsonl", [
        {"type": "custom-title", "sessionId": parent, "customTitle": "Основная задача"},
        dict(event, message=dict(event["message"], usage={"input_tokens": 1})), event,
    ])
    child = _assistant("m2", END.isoformat(), 10, "unpriced-model")
    child["sessionId"] = parent
    make_claude_root(f"Project/{parent}/subagents", "agent-child.jsonl", [child])
    result = Analyzer().analyze(Period("p", "", START, END), collect_quota=False)
    assert len(result.threads) == 1
    row = result.threads[0]
    assert (row.title, row.project, row.subagents, row.total_tokens) == ("Основная задача", "Project", 1, 58)
    assert result.total_cost == pytest.approx((20 * 4 + 10 * 20 + 3 * .20 + 5 * 8) / 1e6)
    assert sum(m.total_tokens for m in result.model_rows) == sum(b.total_tokens for b in result.timeline) == 58
    assert any("unpriced-model" in warning for warning in result.warnings)
