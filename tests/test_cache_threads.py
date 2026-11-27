import datetime as dt
import json
import os
import pathlib
import sqlite3

import pytest

from claude_usage_manager import cache_threads as ct
from claude_usage_manager.cache_threads import (
    CacheThread,
    ThreadScanner,
    cache_hue,
    format_remaining,
    select_threads,
)

NOW = dt.datetime(2026, 11, 12, 10, 0, 0, tzinfo=dt.timezone.utc)


def iso(offset_seconds: float) -> str:
    return (NOW + dt.timedelta(seconds=offset_seconds)).isoformat().replace("+00:00", "Z")


def write_lines(path: pathlib.Path, records, mtime_offset: float = 0.0) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    stamp = (NOW + dt.timedelta(seconds=mtime_offset)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def asst(offset, msg_id, *, one_hour=0, five_min=0, read=0, sidechain=False, legacy=None, cwd=r"C:\Workspace\Clients\Example\trade_demo", session="sess-1", entrypoint="claude-desktop"):
    usage = {
        "input_tokens": 3,
        "output_tokens": 10,
        "cache_read_input_tokens": read,
    }
    if legacy is None:
        usage["cache_creation_input_tokens"] = one_hour + five_min
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": five_min,
            "ephemeral_1h_input_tokens": one_hour,
        }
    else:
        usage["cache_creation_input_tokens"] = legacy
    return {
        "type": "assistant", "timestamp": iso(offset), "sessionId": session, "cwd": cwd,
        "entrypoint": entrypoint, "isSidechain": sidechain,
        "message": {"id": msg_id, "role": "assistant", "usage": usage},
    }


def req(offset, session="sess-1"):
    return {"type": "api-request", "timestamp": iso(offset), "sessionId": session, "querySource": "sdk"}


def user(offset, text, cwd=r"C:\Workspace\Clients\Example\trade_demo", session="sess-1"):
    return {
        "type": "user", "timestamp": iso(offset), "sessionId": session, "cwd": cwd,
        "entrypoint": "claude-desktop", "message": {"role": "user", "content": text},
    }


@pytest.fixture
def dirs(tmp_path):
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    (claude / "projects").mkdir(parents=True)
    codex.mkdir()
    return claude, codex


def scanner(dirs, codex_ttl=300):
    return ThreadScanner(claude_dir=dirs[0], codex_dir=dirs[1], codex_ttl=codex_ttl)


def claude_file(dirs, name="sess-1.jsonl", folder="proj"):
    return dirs[0] / "projects" / folder / name


# ------------------------------- Claude ------------------------------------ #
def test_claude_one_hour_tier_starts_at_response_start(dirs):
    write_lines(claude_file(dirs), [
        user(-300, "Расскажи про остатки на складе"),
        asst(-120, "m1", one_hour=500),
    ])
    threads = scanner(dirs).scan(NOW)["claude"]
    assert len(threads) == 1
    thread = threads[0]
    assert thread.ttl_seconds == 3600 and not thread.estimated
    assert thread.started_at == NOW - dt.timedelta(seconds=120)
    assert thread.remaining(NOW) == 3600 - 120
    assert thread.project == "trade_demo"
    assert thread.environment == "Desktop"
    assert thread.title == "Расскажи про остатки на складе"


def test_claude_timer_starts_at_request_not_at_first_response_chunk(dirs):
    write_lines(claude_file(dirs), [req(-300), asst(-200, "m1", one_hour=10)])
    thread = scanner(dirs).scan(NOW)["claude"][0]
    assert thread.started_at == NOW - dt.timedelta(seconds=300)
    assert thread.remaining(NOW) == 3300


def test_claude_in_flight_request_after_last_response_restarts_the_timer(dirs):
    write_lines(claude_file(dirs), [req(-3000), asst(-2990, "m1", one_hour=10), req(-60)])
    thread = scanner(dirs).scan(NOW)["claude"][0]
    assert thread.started_at == NOW - dt.timedelta(seconds=60)
    assert thread.remaining(NOW) == 3540 and not thread.cooled(NOW)


def test_claude_request_of_previous_turn_is_not_used_for_latest_response(dirs):
    write_lines(claude_file(dirs), [
        req(-3000), asst(-2990, "m1", one_hour=10),
        req(-1000), asst(-990, "m2", read=10),
    ])
    assert scanner(dirs).scan(NOW)["claude"][0].started_at == NOW - dt.timedelta(seconds=1000)


def test_claude_chunks_of_one_message_use_earliest_timestamp(dirs):
    write_lines(claude_file(dirs), [
        asst(-200, "m1", one_hour=100),
        asst(-90, "m2", one_hour=100),
        asst(-80, "m2", one_hour=100),
        asst(-70, "m2", one_hour=100),
    ])
    thread = scanner(dirs).scan(NOW)["claude"][0]
    assert thread.started_at == NOW - dt.timedelta(seconds=90)


def test_claude_tier_is_taken_from_last_cache_write_when_last_response_only_reads(dirs):
    write_lines(claude_file(dirs), [
        asst(-400, "m1", one_hour=800),
        asst(-100, "m2", read=800),
    ])
    thread = scanner(dirs).scan(NOW)["claude"][0]
    assert thread.ttl_seconds == 3600 and not thread.estimated
    assert thread.started_at == NOW - dt.timedelta(seconds=100)


def test_claude_five_minute_tier(dirs):
    write_lines(claude_file(dirs), [asst(-60, "m1", five_min=900)])
    thread = scanner(dirs).scan(NOW)["claude"][0]
    assert thread.ttl_seconds == 300 and not thread.estimated
    assert thread.remaining(NOW) == 240


def test_claude_larger_written_tier_wins_when_both_present(dirs):
    write_lines(claude_file(dirs), [asst(-10, "m1", one_hour=100, five_min=900)])
    assert scanner(dirs).scan(NOW)["claude"][0].ttl_seconds == 300


def test_claude_legacy_usage_without_tier_is_estimated_five_minutes(dirs):
    write_lines(claude_file(dirs), [asst(-30, "m1", legacy=700)])
    thread = scanner(dirs).scan(NOW)["claude"][0]
    assert thread.ttl_seconds == 300 and thread.estimated


def test_claude_reads_only_without_any_write_is_estimated_five_minutes(dirs):
    write_lines(claude_file(dirs), [asst(-30, "m1", read=700)])
    thread = scanner(dirs).scan(NOW)["claude"][0]
    assert thread.ttl_seconds == 300 and thread.estimated


def test_claude_response_without_cache_tokens_is_not_listed(dirs):
    write_lines(claude_file(dirs), [asst(-30, "m1")])
    assert scanner(dirs).scan(NOW)["claude"] == []


def test_claude_sidechain_and_synthetic_messages_are_ignored(dirs):
    write_lines(claude_file(dirs), [
        asst(-200, "m1", one_hour=100),
        asst(-20, "m2", five_min=100, sidechain=True),
        asst(-10, "synthetic-1", five_min=100),
    ])
    thread = scanner(dirs).scan(NOW)["claude"][0]
    assert thread.started_at == NOW - dt.timedelta(seconds=200)
    assert thread.ttl_seconds == 3600


def test_claude_title_prefers_custom_title_then_first_prompt_then_id(dirs):
    path = claude_file(dirs)
    write_lines(path, [
        user(-300, "Первый содержательный запрос пользователя"),
        asst(-120, "m1", one_hour=100),
        {"type": "custom-title", "customTitle": "Название треда из приложения", "sessionId": "sess-1"},
    ])
    assert scanner(dirs).scan(NOW)["claude"][0].title == "Название треда из приложения"

    write_lines(path, [user(-300, "Первый содержательный запрос пользователя"), asst(-120, "m1", one_hour=100)])
    assert scanner(dirs).scan(NOW)["claude"][0].title == "Первый содержательный запрос пользователя"

    write_lines(path, [asst(-120, "m1", one_hour=100)])
    assert scanner(dirs).scan(NOW)["claude"][0].title == "Тред sess-1"


def test_claude_project_and_environment_labels(dirs):
    write_lines(claude_file(dirs, "sess-1.jsonl", "p1"), [
        asst(-10, "m1", one_hour=10, cwd=r"C:\Users\x\AppData\Local\Claude-Data\scratch-workspaces\abc\scratch-2026"),
    ])
    other = asst(-10, "m2", one_hour=10, cwd="C:/Workspace/scratch-workspaces/sample-tools")
    other["entrypoint"] = "cli"
    other["sessionId"] = "sess-2"
    write_lines(claude_file(dirs, "sess-2.jsonl", "p2"), [other])
    by_session = {t.session_id: t for t in scanner(dirs).scan(NOW)["claude"]}
    assert by_session["sess-1"].project == "без папки"
    assert by_session["sess-2"].project == "sample-tools"
    assert by_session["sess-2"].environment == "терминал"


def test_claude_old_files_are_skipped_by_mtime(dirs):
    write_lines(claude_file(dirs), [asst(-10, "m1", one_hour=10)], mtime_offset=-(3600 + 600 + 60))
    assert scanner(dirs).scan(NOW)["claude"] == []


def test_claude_large_last_line_grows_the_tail(dirs, monkeypatch):
    monkeypatch.setattr(ct, "TAIL_BYTES", 400)
    big = asst(-50, "m1", one_hour=10)
    big["message"]["content"] = [{"type": "text", "text": "я" * 5000}]
    write_lines(claude_file(dirs), [asst(-500, "m0", five_min=10), big])
    thread = scanner(dirs).scan(NOW)["claude"][0]
    assert thread.started_at == NOW - dt.timedelta(seconds=50) and thread.ttl_seconds == 3600


def test_scanner_reparses_only_changed_files(dirs, monkeypatch):
    path = write_lines(claude_file(dirs), [asst(-100, "m1", one_hour=10)])
    calls = []
    original = ct._parse_claude_file

    def counting(file, meta):
        calls.append(file)
        return original(file, meta)

    monkeypatch.setattr(ct, "_parse_claude_file", counting)
    s = scanner(dirs)
    s.scan(NOW)
    s.scan(NOW)
    assert len(calls) == 1
    write_lines(path, [asst(-100, "m1", one_hour=10), asst(-5, "m2", one_hour=10)])
    thread = s.scan(NOW)["claude"][0]
    assert len(calls) == 2
    assert thread.started_at == NOW - dt.timedelta(seconds=5)


def test_scanner_ignores_unreadable_json_lines_and_missing_dirs(dirs):
    path = claude_file(dirs)
    write_lines(path, [asst(-100, "m1", one_hour=10)])
    with path.open("a", encoding="utf-8") as stream:
        stream.write("{битая строка\n")
    os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    assert len(scanner(dirs).scan(NOW)["claude"]) == 1
    empty = ThreadScanner(claude_dir=dirs[0] / "нет", codex_dir=dirs[1] / "нет")
    assert empty.scan(NOW) == {"claude": [], "codex": [], "grok": [], "antigravity": []}


# ------------------------ Записи приложения Claude Desktop ------------------- #
def desktop_meta(dirs, name, cli_id, *, archived=False, title="Название из приложения", forked_from=None):
    path = dirs[0].parent / "desktop" / "org" / "acct" / f"local_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "sessionId": f"local_{name}", "cliSessionId": cli_id, "isArchived": archived, "title": title,
        "priorCliSessionIds": ["old-cli-id"],
    }
    if forked_from:
        record["forkedFromSessionId"] = forked_from
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def desktop_scanner(dirs):
    return ThreadScanner(claude_dir=dirs[0], codex_dir=dirs[1], desktop_dirs=[dirs[0].parent / "desktop"])


def test_archived_desktop_thread_is_hidden_and_active_one_gets_app_title(dirs):
    write_lines(claude_file(dirs, "live-1.jsonl", "p1"), [asst(-100, "m1", one_hour=10, session="live-1")])
    write_lines(claude_file(dirs, "gone-1.jsonl", "p2"), [asst(-100, "m2", one_hour=10, session="gone-1")])
    desktop_meta(dirs, "live", "live-1", title="Правка обмена с сайтом")
    desktop_meta(dirs, "gone", "gone-1", archived=True, title="Старый архивный тред")
    threads = desktop_scanner(dirs).scan(NOW)["claude"]
    assert [(t.session_id, t.title) for t in threads] == [("live-1", "Правка обмена с сайтом")]


def test_fork_with_same_title_gets_fork_mark(dirs):
    write_lines(claude_file(dirs, "orig-1.jsonl", "p1"), [asst(-100, "m1", one_hour=10, session="orig-1")])
    write_lines(claude_file(dirs, "fork-1.jsonl", "p2"), [asst(-90, "m2", one_hour=10, session="fork-1")])
    desktop_meta(dirs, "orig", "orig-1", title="Тестирование заказов")
    desktop_meta(dirs, "fork", "fork-1", title="Тестирование заказов", forked_from="local_orig")
    titles = {t.session_id: t.title for t in desktop_scanner(dirs).scan(NOW)["claude"]}
    assert titles == {"orig-1": "Тестирование заказов", "fork-1": "Тестирование заказов (fork)"}


def test_desktop_thread_absent_in_app_records_is_hidden_but_terminal_thread_is_kept(dirs):
    write_lines(claude_file(dirs, "live-1.jsonl", "p1"), [asst(-100, "m1", one_hour=10, session="live-1")])
    write_lines(claude_file(dirs, "deleted-1.jsonl", "p2"), [asst(-100, "m2", one_hour=10, session="deleted-1")])
    write_lines(claude_file(dirs, "cli-1.jsonl", "p3"), [asst(-100, "m3", one_hour=10, session="cli-1", entrypoint="cli")])
    write_lines(claude_file(dirs, "old-cli-id.jsonl", "p4"), [asst(-100, "m4", one_hour=10, session="old-cli-id")])
    desktop_meta(dirs, "live", "live-1")
    ids = sorted(t.session_id for t in desktop_scanner(dirs).scan(NOW)["claude"])
    assert ids == ["cli-1", "live-1"]


def test_filter_is_off_when_no_app_records_were_found(dirs):
    write_lines(claude_file(dirs, "any-1.jsonl"), [asst(-100, "m1", one_hour=10, session="any-1")])
    (dirs[0].parent / "desktop").mkdir()
    threads = desktop_scanner(dirs).scan(NOW)["claude"]
    assert [t.session_id for t in threads] == ["any-1"]
    assert ThreadScanner(claude_dir=dirs[0], codex_dir=dirs[1]).scan(NOW)["claude"][0].session_id == "any-1"


def test_archiving_in_the_app_removes_thread_on_next_scan_and_empty_title_keeps_log_title(dirs):
    write_lines(claude_file(dirs, "live-1.jsonl"), [user(-300, "Запрос из журнала про остатки", session="live-1"), asst(-100, "m1", one_hour=10, session="live-1")])
    meta = desktop_meta(dirs, "live", "live-1", title="")
    s = desktop_scanner(dirs)
    assert s.scan(NOW)["claude"][0].title == "Запрос из журнала про остатки"
    desktop_meta(dirs, "live", "live-1", archived=True, title="Заархивирован позже")
    os.utime(meta, (NOW.timestamp() + 5, NOW.timestamp() + 5))
    assert s.scan(NOW)["claude"] == []


def test_desktop_session_dirs_are_discovered_from_local_app_data(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Каталог Claude-Data есть только в Windows")
    local = tmp_path / "local"
    target = local / "Claude-Data" / "claude_abc" / "claude-code-sessions"
    target.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert ct.desktop_session_dirs() == [target]


# -------------------------------- Codex ------------------------------------ #
def codex_records(*, input_tokens=5000, cached=4000, last_offset=-100, originator="Codex Desktop", source="vscode", cwd=r"C:\Workspace\demo-server", session="cx-1", prompts=("Проверь настройки сервера и скажи что не так",), model=None, tail_model=None):
    return [
        {"timestamp": iso(-900), "type": "session_meta", "payload": {
            "id": session, "session_id": session, "cwd": cwd, "originator": originator, "source": source}},
        *([{"timestamp": iso(-895), "type": "turn_context", "payload": {"turn_id": "t1", "model": model}}] if model else []),
        *[
            {"timestamp": iso(-890 + index), "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": prompt}]}}
            for index, prompt in enumerate(prompts)
        ],
        *([{"timestamp": iso(last_offset - 5), "type": "turn_context", "payload": {"turn_id": "t2", "model": tail_model}}] if tail_model else []),
        {"timestamp": iso(last_offset), "type": "event_msg", "payload": {
            "type": "token_count", "info": {"last_token_usage": {
                "input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": 50}}}},
    ]


def codex_file(dirs, day="2026/11/12", name="rollout-1.jsonl"):
    return dirs[1] / day / name


def test_codex_thread_uses_estimated_ttl_and_session_meta(dirs):
    write_lines(codex_file(dirs), codex_records())
    threads = scanner(dirs, codex_ttl=600).scan(NOW)["codex"]
    assert len(threads) == 1
    thread = threads[0]
    assert thread.estimated and thread.ttl_seconds == 600
    assert thread.started_at == NOW - dt.timedelta(seconds=100)
    assert thread.project == "demo-server"
    assert thread.environment == "Desktop"
    assert thread.title == "Проверь настройки сервера и скажи что не так"
    assert thread.session_id == "cx-1"


def test_codex_gpt56_and_newer_has_documented_30_minute_ttl_not_an_estimate(dirs):
    write_lines(codex_file(dirs), codex_records(model="gpt-6.1-sol"))
    thread = scanner(dirs, codex_ttl=600).scan(NOW)["codex"][0]
    assert thread.ttl_seconds == 1800 and not thread.estimated
    assert "30 минут" in thread.basis and thread.remaining(NOW) == 1700


def test_codex_model_of_the_last_turn_decides_and_older_models_stay_estimates(dirs):
    write_lines(codex_file(dirs, name="new.jsonl"), codex_records(session="cx-1", model="gpt-5.5", tail_model="gpt-6-astra"))
    write_lines(codex_file(dirs, name="old.jsonl"), codex_records(session="cx-2", model="gpt-6.1-sol", tail_model="gpt-5.1-codex"))
    write_lines(codex_file(dirs, name="none.jsonl"), codex_records(session="cx-3"))
    threads = {t.session_id: t for t in scanner(dirs, codex_ttl=900).scan(NOW)["codex"]}
    assert (threads["cx-1"].ttl_seconds, threads["cx-1"].estimated) == (1800, False)
    assert (threads["cx-2"].ttl_seconds, threads["cx-2"].estimated) == (900, True)
    assert (threads["cx-3"].ttl_seconds, threads["cx-3"].estimated) == (900, True)
    assert "оценка" in threads["cx-3"].basis


def test_codex_model_is_found_far_from_both_ends_of_a_big_log(dirs, monkeypatch):
    monkeypatch.setattr(ct, "TAIL_BYTES", 3000)
    monkeypatch.setattr(ct, "HEAD_BYTES", 3000)
    filler = [{"timestamp": iso(-800 + i), "type": "response_item", "payload": {"type": "function_call_output", "output": "x" * 400}} for i in range(60)]
    records = codex_records(model="gpt-6.1-sol")
    write_lines(codex_file(dirs), records[:3] + filler + records[3:])
    assert scanner(dirs).scan(NOW)["codex"][0].estimated is False


def test_codex_model_scan_reads_only_new_bytes_and_skips_a_partial_last_line(tmp_path):
    path = tmp_path / "rollout.jsonl"
    first = json.dumps({"type": "turn_context", "payload": {"model": "gpt-5.5"}}) + "\n"
    second = json.dumps({"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}}) + "\n"
    path.write_bytes(first.encode())
    offset, model = ct._scan_codex_model(path, 0, "")
    assert (offset, model) == (len(first.encode()), "gpt-5.5")
    with path.open("ab") as stream:
        stream.write(second[:20].encode())
    assert ct._scan_codex_model(path, offset, model) == (offset, "gpt-5.5")
    with path.open("ab") as stream:
        stream.write(second[20:].encode())
    new_offset, model = ct._scan_codex_model(path, offset, model)
    assert model == "gpt-6.1-sol" and new_offset == path.stat().st_size


def test_codex_documented_ttl_by_model_name():
    ttl = ct.codex_documented_ttl
    assert [ttl(m) for m in ("gpt-6.1-sol", "gpt-6-astra", "gpt-5.6", "GPT-5.7-mini", "gpt-10.2")] == [1800] * 5
    assert [ttl(m) for m in ("gpt-5.5", "gpt-5.5-pro", "gpt-5.1-codex", "gpt-5-codex", "gpt-4.1", "o3", "")] == [None] * 7


def test_default_scanner_never_reads_antigravity_and_ignores_the_personal_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    assert ThreadScanner()._antigravity_dir is None
    assert ThreadScanner().scan(NOW)["antigravity"] == []


def test_configured_scanner_reads_desktop_name_and_archive_from_selected_profile(monkeypatch, tmp_path):
    from claude_usage_manager import provider_settings

    first, second = tmp_path / "one", tmp_path / "two"
    for root, archived in ((first, False), (second, True)):
        write_lines(root / "projects" / "demo" / "shared.jsonl", [asst(-40, "msg", one_hour=10, session="shared")])
        write_lines(root / "claude-code-sessions" / "account" / "local_shared.json",
                    [{"cliSessionId": "shared", "title": "Имя из профиля", "isArchived": archived}])
    selected = first
    monkeypatch.setattr(provider_settings, "provider_enabled", lambda key: key == "claude")
    monkeypatch.setattr(provider_settings, "provider_profile", lambda _: selected)
    monkeypatch.setattr(ct, "desktop_session_dirs", lambda: pytest.fail("Обращение к другому профилю"))
    scanner = ThreadScanner()
    assert [row.title for row in scanner.scan(NOW)["claude"]] == ["Имя из профиля"]
    selected = second
    assert scanner.scan(NOW)["claude"] == []


def test_codex_title_skips_injected_instructions_and_environment(dirs):
    write_lines(codex_file(dirs), codex_records(prompts=(
        "# AGENTS.md instructions for C:\\Workspace\\demo-server\n\n<INSTRUCTIONS>читай правила</INSTRUCTIONS>",
        "<environment_context><cwd>C:\\Workspace</cwd></environment_context>",
        "Настрой резервное копирование сервера",
    )))
    assert scanner(dirs).scan(NOW)["codex"][0].title == "Настрой резервное копирование сервера"


def codex_state_db(dirs, rows):
    connection = sqlite3.connect(dirs[1].parent / "state_5.sqlite")
    connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT, archived INTEGER, updated_at INTEGER)")
    connection.executemany("INSERT INTO threads VALUES (?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


def test_codex_state_db_supplies_names_and_hides_archived_threads(dirs):
    for session in ("cx-1", "cx-2", "cx-3", "cx-4"):
        write_lines(codex_file(dirs, name=f"{session}.jsonl"), codex_records(session=session))
    codex_state_db(dirs, [
        ("cx-1", "Мое имя треда", "Автоназвание", 0, 4),
        ("cx-2", "", "# Files mentioned by the user:\n\n## отчет.xlsx\nСверь итоги", 0, 3),
        ("cx-3", "", "Архивный", 1, 2),
        ("cx-4", "", "", 0, 1),
    ])
    threads = {t.session_id: t for t in scanner(dirs).scan(NOW)["codex"]}
    assert set(threads) == {"cx-1", "cx-2", "cx-4"}
    assert threads["cx-1"].title == "Мое имя треда"
    assert threads["cx-2"].title == "## отчет.xlsx Сверь итоги"
    assert threads["cx-4"].title == "Проверь настройки сервера и скажи что не так"


def test_codex_state_db_with_unknown_schema_keeps_log_titles(dirs):
    write_lines(codex_file(dirs), codex_records())
    connection = sqlite3.connect(dirs[1].parent / "state_5.sqlite")
    connection.execute("CREATE TABLE threads (id TEXT)")
    connection.commit()
    connection.close()
    assert scanner(dirs).scan(NOW)["codex"][0].title == "Проверь настройки сервера и скажи что не так"


def test_codex_environment_variants(dirs):
    write_lines(codex_file(dirs, name="a.jsonl"), codex_records(originator="codex_cli_rs", source="cli"))
    write_lines(codex_file(dirs, name="b.jsonl"), codex_records(originator="codex_vscode", source="vscode"))
    environments = sorted(t.environment for t in scanner(dirs).scan(NOW)["codex"])
    assert environments == ["VS Code", "терминал"]


def test_codex_without_cacheable_prompt_is_not_listed(dirs):
    write_lines(codex_file(dirs), codex_records(input_tokens=200, cached=0))
    assert scanner(dirs).scan(NOW)["codex"] == []


def test_codex_without_token_events_is_not_listed(dirs):
    write_lines(codex_file(dirs), codex_records()[:2])
    assert scanner(dirs).scan(NOW)["codex"] == []


def test_codex_very_old_day_folders_are_not_walked(dirs):
    write_lines(codex_file(dirs, day="2026/08/01"), codex_records())
    assert scanner(dirs).scan(NOW)["codex"] == []


def test_codex_stale_file_mtime_does_not_hide_a_live_thread(dirs):
    """На Windows время правки rollout отстаёт от событий на часы: отсев по нему прятал живой тред."""
    write_lines(codex_file(dirs), codex_records(last_offset=-300), mtime_offset=-6000)
    threads = scanner(dirs).scan(NOW)["codex"]
    assert len(threads) == 1 and threads[0].started_at == NOW - dt.timedelta(seconds=300)


def test_codex_session_continued_in_an_older_day_folder_is_listed(dirs):
    write_lines(codex_file(dirs, day="2026/11/03"), codex_records(last_offset=-20), mtime_offset=-9000)
    threads = scanner(dirs).scan(NOW)["codex"]
    assert len(threads) == 1 and threads[0].remaining(NOW) > 0


def test_default_ttl_estimates_follow_the_log_evidence():
    assert (ct.CODEX_TTL_DEFAULT, ct.GROK_TTL_DEFAULT, ct.ANTIGRAVITY_TTL_DEFAULT) == (3600, 900, 300)


def test_codex_ttl_change_invalidates_cached_threads(dirs):
    write_lines(codex_file(dirs), codex_records())
    s = scanner(dirs, codex_ttl=300)
    assert s.scan(NOW)["codex"][0].ttl_seconds == 300
    s.set_codex_ttl(900)
    assert s.scan(NOW)["codex"][0].ttl_seconds == 900


# ------------------------------- Grok Build -------------------------------- #
def grok_event(offset, sid, *, prompt=50000, cached=48000, msg="shell.turn.inference_done"):
    return {
        "ts": iso(offset), "src": "shell", "lvl": "info", "sid": sid, "msg": msg,
        "ctx": {"prompt_tokens": prompt, "cached_prompt_tokens": cached, "completion_tokens": 30},
    }


def grok_home(tmp_path, events, sessions=(("g-1", "DEMO-42. Проверка", r"C:\Workspace\Tools"),)):
    home = tmp_path / "grok"
    write_lines(home / "logs" / "unified.jsonl", events)
    for session_id, title, cwd in sessions:
        folder = home / "sessions" / "C%3A%2FWorkspace%2FTools" / session_id
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text(json.dumps({
            "info": {"id": session_id, "cwd": cwd}, "session_summary": "краткое " + title,
            "generated_title": title, "last_active_at": iso(-50),
        }, ensure_ascii=False), encoding="utf-8")
    return home


def grok_scanner(dirs, home, **kwargs):
    return ThreadScanner(claude_dir=dirs[0], codex_dir=dirs[1], grok_dir=home, **kwargs)


def test_grok_thread_takes_last_request_title_and_project(dirs, tmp_path):
    home = grok_home(tmp_path, [grok_event(-700, "g-1"), grok_event(-120, "g-1"), grok_event(-60, "other")])
    threads = grok_scanner(dirs, home).scan(NOW)["grok"]
    assert [t.session_id for t in threads] == ["g-1"]
    thread = threads[0]
    assert thread.agent == "grok" and thread.estimated and thread.ttl_seconds == ct.GROK_TTL_DEFAULT
    assert thread.started_at == NOW - dt.timedelta(seconds=120)
    assert thread.title == "DEMO-42. Проверка" and thread.project == "Tools"
    assert thread.environment == "терминал"


def test_grok_requests_without_cacheable_prompt_or_other_events_are_ignored(dirs, tmp_path):
    home = grok_home(tmp_path, [grok_event(-100, "g-1", prompt=300, cached=0), grok_event(-50, "g-1", msg="shell.turn.started")])
    assert grok_scanner(dirs, home).scan(NOW)["grok"] == []


def test_grok_log_change_is_picked_up_and_ttl_is_configurable(dirs, tmp_path):
    home = grok_home(tmp_path, [grok_event(-1000, "g-1")])
    s = grok_scanner(dirs, home, grok_ttl=1800)
    assert s.scan(NOW)["grok"][0].remaining(NOW) == 800
    log = home / "logs" / "unified.jsonl"
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(grok_event(-30, "g-1")) + "\n")
    assert s.scan(NOW)["grok"][0].remaining(NOW) == 1770
    s.set_grok_ttl(900)
    assert s.scan(NOW)["grok"][0].remaining(NOW) == 870


def test_grok_tail_drops_a_partial_first_line_and_survives_garbage(dirs, tmp_path, monkeypatch):
    home = grok_home(tmp_path, [grok_event(-90, "g-1")])
    with (home / "logs" / "unified.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("{шум shell.turn.inference_done\n")
    monkeypatch.setattr(ct, "GROK_LOG_TAIL_BYTES", 400)
    assert [t.session_id for t in grok_scanner(dirs, home).scan(NOW)["grok"]] == ["g-1"]


def test_grok_session_without_summary_and_missing_files_are_skipped(dirs, tmp_path):
    home = grok_home(tmp_path, [grok_event(-60, "gone")])
    assert grok_scanner(dirs, home).scan(NOW)["grok"] == []
    assert grok_scanner(dirs, tmp_path / "нет").scan(NOW)["grok"] == []


def test_scanner_with_explicit_claude_dir_never_reads_personal_grok_or_antigravity(dirs, monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path / "нет-дома"))
    result = ThreadScanner(claude_dir=dirs[0], codex_dir=dirs[1]).scan(NOW)
    assert result["grok"] == [] and result["antigravity"] == []


# ------------------------------- Antigravity ------------------------------- #
def antigravity_home(tmp_path, rows):
    home = tmp_path / "antigravity"
    home.mkdir()
    connection = sqlite3.connect(home / ct.ANTIGRAVITY_DB_NAME)
    connection.execute(
        "CREATE TABLE conversation_summaries (conversation_id text, title text NOT NULL DEFAULT '',"
        " preview text NOT NULL DEFAULT '', last_modified_time datetime NOT NULL,"
        " last_user_input_time datetime, workspace_uris text NOT NULL, source text)"
    )
    connection.executemany("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()
    return home


def agy_row(session, modified_offset, *, title="", preview="Ответь ок", user_offset=None, uri="file:///C:/Workspace/sample-tools", source="CORTEX_TRAJECTORY_SOURCE_CLI"):
    modified = (NOW + dt.timedelta(seconds=modified_offset)).strftime("%Y-%m-%d %H:%M:%S.8154835+00:00")
    if user_offset is None:
        user_time = "0001-01-01 00:00:00+00:00"
    else:
        user_time = (NOW + dt.timedelta(seconds=user_offset)).strftime("%Y-%m-%d %H:%M:%S.1234567+00:00")
    return (session, title, preview, modified, user_time, json.dumps([uri]), source)


def agy_scanner(dirs, home, **kwargs):
    return ThreadScanner(claude_dir=dirs[0], codex_dir=dirs[1], antigravity_dir=home, **kwargs)


def test_antigravity_threads_come_from_conversation_time_with_estimated_ttl(dirs, tmp_path):
    home = antigravity_home(tmp_path, [
        agy_row("c-1", -200, preview="Контрольный тест расхода токенов после синхронизации"),
        agy_row("c-2", -400, title="Свое название", uri="file:///C:/Workspace/My%20Tools", source="CORTEX_TRAJECTORY_SOURCE_IDE"),
    ])
    threads = {t.session_id: t for t in agy_scanner(dirs, home).scan(NOW)["antigravity"]}
    first, second = threads["c-1"], threads["c-2"]
    assert first.agent == "antigravity" and first.estimated and first.ttl_seconds == ct.ANTIGRAVITY_TTL_DEFAULT
    assert abs((first.started_at - (NOW - dt.timedelta(seconds=200))).total_seconds()) < 1
    assert first.title == "Контрольный тест расхода токенов после синхронизации"
    assert first.project == "sample-tools" and first.environment == "терминал"
    assert second.title == "Свое название" and second.project == "My Tools" and second.environment == "Antigravity"


def test_antigravity_later_user_input_wins_and_zero_date_is_ignored(dirs, tmp_path):
    home = antigravity_home(tmp_path, [agy_row("c-1", -500, user_offset=-100), agy_row("c-2", -500)])
    threads = {t.session_id: t for t in agy_scanner(dirs, home).scan(NOW)["antigravity"]}
    assert abs(threads["c-1"].remaining(NOW) - 200) < 1
    assert abs(threads["c-2"].started_at.timestamp() - (NOW.timestamp() - 500)) < 1


def test_antigravity_database_is_opened_read_only_and_ttl_change_applies(dirs, tmp_path):
    home = antigravity_home(tmp_path, [agy_row("c-1", -100)])
    db = home / ct.ANTIGRAVITY_DB_NAME
    before = db.read_bytes()
    s = agy_scanner(dirs, home)
    assert s.scan(NOW)["antigravity"][0].ttl_seconds == 300
    s.set_antigravity_ttl(900)
    assert s.scan(NOW)["antigravity"][0].ttl_seconds == 900
    assert db.read_bytes() == before


def test_antigravity_changed_database_is_reread_and_broken_or_missing_one_is_empty(dirs, tmp_path):
    home = antigravity_home(tmp_path, [agy_row("c-1", -100)])
    s = agy_scanner(dirs, home)
    assert len(s.scan(NOW)["antigravity"]) == 1
    connection = sqlite3.connect(home / ct.ANTIGRAVITY_DB_NAME)
    connection.execute("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?)", agy_row("c-2", -50))
    connection.commit()
    connection.close()
    assert len(s.scan(NOW)["antigravity"]) == 2
    (home / ct.ANTIGRAVITY_DB_NAME).write_bytes(b"not a database" * 100)
    assert agy_scanner(dirs, home).scan(NOW)["antigravity"] == []
    assert agy_scanner(dirs, tmp_path / "нет").scan(NOW)["antigravity"] == []


# ------------------------- Выбор, цвет, текст ------------------------------ #
def make(session, started_offset, ttl):
    return CacheThread(
        agent="claude", session_id=session, title=session, project="p", environment="Desktop",
        started_at=NOW + dt.timedelta(seconds=started_offset), ttl_seconds=ttl,
    )


def test_select_threads_orders_by_fraction_cooled_last_and_limits():
    hot = make("hot", -3500, 3600)        # осталось 100 с, доля 0.028
    mid = make("mid", -300, 3600)         # доля 0.92
    short = make("short", -250, 300)      # осталось 50 с, доля 0.167
    just_cold = make("just_cold", -3700, 3600)   # остыл 100 с назад
    long_cold = make("long_cold", -5000, 3600)   # остыл давно
    shown, hidden = select_threads([mid, long_cold, hot, just_cold, short], NOW)
    assert [t.session_id for t in shown] == ["hot", "short", "mid", "just_cold"]
    assert hidden == 0
    shown, hidden = select_threads([mid, hot, short, just_cold], NOW, limit=2)
    assert [t.session_id for t in shown] == ["hot", "short"] and hidden == 2
    everything, none_hidden = select_threads([mid, hot, short, just_cold], NOW)
    assert len(everything) == 4 and none_hidden == 0


def test_fraction_remaining_and_cooled():
    thread = make("t", -150, 300)
    assert thread.remaining(NOW) == 150 and thread.fraction(NOW) == 0.5 and not thread.cooled(NOW)
    late = NOW + dt.timedelta(seconds=200)
    assert thread.remaining(late) == 0 and thread.fraction(late) == 0 and thread.cooled(late)


def test_cache_hue_and_format():
    assert cache_hue(1.0) == 130 and cache_hue(0.6) == 130
    assert cache_hue(0.3) == 65 and cache_hue(0.0) == 0 and cache_hue(-1) == 0
    assert format_remaining(0) == "остыл"
    assert format_remaining(95.9) == "1:35"
    assert format_remaining(95, estimated=True) == "~1:35"
    assert format_remaining(3600) == "60:00"


# -------------------- Регрессии по ревью Fable 5.2 (CLA-5) ------------------ #
def test_fork_journal_takes_its_own_file_name_as_session_id_not_the_parents(dirs):
    """Журнал форка начинается с копии истории родителя: sessionId в ней принадлежит родителю."""
    write_lines(claude_file(dirs, "fork-1.jsonl", "p1"), [
        user(-300, "Запрос из скопированной истории", session="parent-1"),
        asst(-100, "m1", one_hour=10, session="parent-1"),
    ])
    assert [t.session_id for t in scanner(dirs).scan(NOW)["claude"]] == ["fork-1"]


def test_api_error_message_does_not_hide_a_thread_with_a_live_cache(dirs):
    error = asst(-60, "5b0e6d44-aaaa-bbbb-cccc-000000000001", one_hour=0)
    error["message"]["model"] = "<synthetic>"
    error["message"]["usage"] = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    error["isApiErrorMessage"] = True
    write_lines(claude_file(dirs), [asst(-300, "m1", one_hour=500), error])
    threads = scanner(dirs).scan(NOW)["claude"]
    assert len(threads) == 1 and threads[0].started_at == NOW - dt.timedelta(seconds=300)


def test_timestamp_without_timezone_is_read_as_utc_and_does_not_break_the_scan(dirs):
    record = asst(-100, "m1", one_hour=10)
    record["timestamp"] = (NOW - dt.timedelta(seconds=100)).replace(tzinfo=None).isoformat()
    write_lines(claude_file(dirs, "naive-1.jsonl", "p1"), [record])
    write_lines(claude_file(dirs, "ok-1.jsonl", "p2"), [asst(-100, "m2", one_hour=10, session="ok-1")])
    ids = sorted(t.session_id for t in scanner(dirs).scan(NOW)["claude"])
    assert ids == ["naive-1", "ok-1"]


def test_one_broken_journal_is_skipped_and_the_rest_are_listed(dirs, monkeypatch):
    write_lines(claude_file(dirs, "bad-1.jsonl", "p1"), [asst(-100, "m1", one_hour=10)])
    write_lines(claude_file(dirs, "ok-1.jsonl", "p2"), [asst(-100, "m2", one_hour=10, session="ok-1")])
    real = ct._parse_claude_file

    def flaky(path, meta):
        if path.stem == "bad-1":
            raise TypeError("can't compare offset-naive and offset-aware datetimes")
        return real(path, meta)

    monkeypatch.setattr(ct, "_parse_claude_file", flaky)
    assert [t.session_id for t in scanner(dirs).scan(NOW)["claude"]] == ["ok-1"]


def test_title_appears_after_a_session_that_started_with_a_command(dirs):
    path = claude_file(dirs, "cmd-1.jsonl")
    write_lines(path, [user(-400, "/model"), asst(-300, "m1", one_hour=10, session="cmd-1")])
    s = scanner(dirs)
    assert s.scan(NOW)["claude"][0].title == "Тред cmd-1"
    write_lines(path, [user(-400, "/model"), asst(-300, "m1", one_hour=10, session="cmd-1"),
                       user(-200, "Настрой резервное копирование базы", session="cmd-1"), asst(-100, "m2", one_hour=10, session="cmd-1")])
    assert s.scan(NOW)["claude"][0].title == "Настрой резервное копирование базы"


def test_codex_logs_of_long_cooled_threads_are_not_read_for_the_model(dirs, monkeypatch):
    calls = []
    real = ct._scan_codex_model
    monkeypatch.setattr(ct, "_scan_codex_model", lambda *args: calls.append(args[0].name) or real(*args))
    write_lines(codex_file(dirs, name="old.jsonl"), codex_records(session="cx-old", model="gpt-6.1-sol", last_offset=-86400))
    write_lines(codex_file(dirs, name="live.jsonl"), codex_records(session="cx-live", model="gpt-6.1-sol", last_offset=-60))
    scanner(dirs).scan(NOW)
    assert calls == ["live.jsonl"]
