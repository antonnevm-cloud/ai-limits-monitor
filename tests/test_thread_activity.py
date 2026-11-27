import datetime as dt
import json
import sqlite3
import struct

import pytest

from claude_usage_manager import thread_activity as ta
from claude_usage_manager.cache_threads import ThreadScanner
from claude_usage_manager.chromium_leveldb import (
    LOG_BLOCK_SIZE,
    TABLE_MAGIC,
    LevelDbError,
    LevelDbKeyReader,
    snappy_decompress,
)

NOW = dt.datetime(2026, 11, 12, 10, 0, 0, tzinfo=dt.timezone.utc)
DESKTOP_DIR = "claude-code-sessions"
KEY = b"_https://claude.ai\x00\x01epitaxy-unread-v1"


def iso(offset):
    return (NOW + dt.timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


# ----------------------------- LevelDB ------------------------------------- #
def varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def snappy_literal(data):
    """Самый простой допустимый вид Snappy: один литерал."""
    size = len(data) - 1
    if size < 60:
        head = bytes([size << 2])
    else:
        head = bytes([60 << 2, size])
    return varint(len(data)) + head + data


def internal_key(user_key, sequence, kind=1):
    return user_key + struct.pack("<Q", (sequence << 8) | kind)


def block(entries):
    out = bytearray()
    for key, value in entries:
        out += varint(0) + varint(len(key)) + varint(len(value)) + key + value
    out += struct.pack("<I", 0) + struct.pack("<I", 1)
    return bytes(out)


def table(entries_by_block, compress_last=False):
    """Таблица LevelDB: по блоку данных на группу записей, индекс и подпись; крайние блоки можно сжать."""
    body = bytearray()
    index_entries = []
    for number, entries in enumerate(entries_by_block):
        raw = block(entries)
        compressed = compress_last and number == len(entries_by_block) - 1
        payload = snappy_literal(raw) if compressed else raw
        offset = len(body)
        body += payload + bytes([1 if compressed else 0]) + b"\0\0\0\0"
        index_entries.append((entries[-1][0], varint(offset) + varint(len(payload))))
    index = block(index_entries)
    index_offset = len(body)
    body += index + b"\0" + b"\0\0\0\0"
    footer = varint(0) + varint(0) + varint(index_offset) + varint(len(index))
    footer += b"\0" * (TABLE_FOOTER - len(footer) - 8) + TABLE_MAGIC
    return bytes(body) + footer


TABLE_FOOTER = 48


def write_batch(sequence, records):
    out = bytearray(struct.pack("<Q", sequence) + struct.pack("<I", len(records)))
    for kind, key, value in records:
        out += bytes([kind]) + varint(len(key)) + key
        if kind == 1:
            out += varint(len(value)) + value
    return bytes(out)


def log_file(batches, split=False):
    out = bytearray()
    for batch in batches:
        if split and len(batch) > 4:
            half = len(batch) // 2
            for kind, chunk in ((2, batch[:half]), (4, batch[half:])):
                out += b"\0\0\0\0" + struct.pack("<H", len(chunk)) + bytes([kind]) + chunk
        else:
            out += b"\0\0\0\0" + struct.pack("<H", len(batch)) + bytes([1]) + batch
    return bytes(out)


def test_snappy_literal_and_overlapping_copy():
    assert snappy_decompress(snappy_literal(b"hello world")) == b"hello world"
    # "abc" + копия 6 байт со смещением 3 (перекрытие) = "abcabcabc"
    assert snappy_decompress(bytes([9, 8]) + b"abc" + bytes([9, 3])) == b"abcabcabc"
    with pytest.raises(LevelDbError):
        snappy_decompress(bytes([9, 8]) + b"abc" + bytes([9, 9]))  # смещение больше уже распакованного
    with pytest.raises(LevelDbError):
        snappy_decompress(bytes([5, 8]) + b"abc")  # длина не совпала


def test_reader_picks_highest_sequence_across_table_and_log(tmp_path):
    other = b"_https://claude.ai\x00\x01other"
    entries = [[(internal_key(other, 3), b"x"), (internal_key(KEY, 5), b"\x01old")]]
    (tmp_path / "000001.ldb").write_bytes(table(entries))
    (tmp_path / "000002.ldb").write_bytes(table([[(internal_key(KEY, 7), b"\x01mid")]], compress_last=True))
    (tmp_path / "000003.log").write_bytes(log_file([write_batch(9, [(1, KEY, b"\x01new")])]))
    reader = LevelDbKeyReader(KEY)
    assert reader.read(tmp_path) == b"\x01new"
    (tmp_path / "000003.log").write_bytes(log_file([write_batch(9, [(1, KEY, b"\x01new")]), write_batch(12, [(0, KEY, b"")])]))
    assert reader.read(tmp_path) is None  # запись удалена более новой
    (tmp_path / "000003.log").unlink()
    assert reader.read(tmp_path) == b"\x01mid"


def test_reader_without_the_key_returns_none_and_fragmented_log_records_are_joined(tmp_path):
    (tmp_path / "000001.log").write_bytes(log_file([write_batch(4, [(1, b"another", b"v")])], split=True))
    assert LevelDbKeyReader(KEY).read(tmp_path) is None
    (tmp_path / "000001.log").write_bytes(log_file([write_batch(4, [(1, KEY, b"\x01json")])], split=True))
    assert LevelDbKeyReader(KEY).read(tmp_path) == b"\x01json"


def test_reader_ignores_unfinished_log_tail_and_rejects_broken_tables(tmp_path):
    complete = log_file([write_batch(4, [(1, KEY, b"\x01done")])])
    partial = b"\0\0\0\0" + struct.pack("<H", 500) + bytes([1]) + b"short"
    (tmp_path / "000001.log").write_bytes(complete + partial)
    assert LevelDbKeyReader(KEY).read(tmp_path) == b"\x01done"
    (tmp_path / "000002.ldb").write_bytes(b"not a table at all" * 5)
    with pytest.raises(LevelDbError):
        LevelDbKeyReader(KEY).read(tmp_path)
    with pytest.raises(LevelDbError):
        LevelDbKeyReader(KEY).read(tmp_path / "missing")


def test_log_records_do_not_cross_block_boundaries(tmp_path):
    filler = write_batch(1, [(1, b"filler", b"x" * (LOG_BLOCK_SIZE - 7 - 12 - 20))])
    data = log_file([filler]) + b"\0" * 3  # хвост блока короче заголовка пропускается
    padded = data + b"\0" * (LOG_BLOCK_SIZE - len(data)) + log_file([write_batch(2, [(1, KEY, b"\x01v")])])
    (tmp_path / "000001.log").write_bytes(padded)
    assert LevelDbKeyReader(KEY).read(tmp_path) == b"\x01v"


# ------------------------------ Claude ------------------------------------- #
def unread_value(ids, explicit=()):
    return b"\x01" + json.dumps({"state": {"unreadIds": list(ids), "explicitUnreadIds": list(explicit), "stoppedRuns": []}, "version": 0}).encode("latin-1")


def test_parse_unread_ids_reads_both_lists_and_rejects_garbage():
    assert ta.parse_unread_ids(unread_value(["local_a", "local_b"], ["local_c"])) == {"local_a", "local_b", "local_c"}
    assert ta.parse_unread_ids(None) == set()
    with pytest.raises(LevelDbError):
        ta.parse_unread_ids(b"\x01not json")
    with pytest.raises(LevelDbError):
        ta.parse_unread_ids(b"\x01" + b'{"state": 5}')


def test_claude_activity_priority():
    assert ta.claude_activity("waiting", True) == ta.ACTIVITY_NEEDS_REPLY
    assert ta.claude_activity("busy", True) == ta.ACTIVITY_WORKING  # пока процесс занят, тред работает, даже если не просмотрен
    assert ta.claude_activity("busy", False) == ta.ACTIVITY_WORKING
    assert ta.claude_activity("idle", False) == "" and ta.claude_activity(None, False) == ""
    assert ta.claude_activity("idle", True) == ta.ACTIVITY_UNREAD and ta.claude_activity(None, True) == ta.ACTIVITY_UNREAD
    # Итог хода с blocked: true: процесс простаивает, но приложение ждет ответа пользователя
    assert ta.claude_activity("idle", False, True) == ta.ACTIVITY_NEEDS_REPLY
    assert ta.claude_activity("idle", True, True) == ta.ACTIVITY_NEEDS_REPLY
    assert ta.claude_activity("busy", True, True) == ta.ACTIVITY_NEEDS_REPLY
    assert ta.claude_activity(None, False, True) == ta.ACTIVITY_NEEDS_REPLY


def write_process(directory, pid, session, status, proc_start="134389435894606891"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": session, "status": status, "procStart": proc_start,
    }), encoding="utf-8")


def test_read_claude_processes_uses_live_processes_and_strongest_status(tmp_path):
    sessions = tmp_path / "sessions"
    write_process(sessions, 11, "s-1", "busy")
    write_process(sessions, 12, "s-1", "waiting")
    write_process(sessions, 13, "s-2", "idle")
    write_process(sessions, 14, "s-dead", "busy")
    (sessions / "15.json").write_text("{broken", encoding="utf-8")
    (sessions / "16.json").write_text(json.dumps({"pid": 16, "sessionId": "s-x", "status": "weird"}), encoding="utf-8")
    seen = []

    def alive(pid, started):
        seen.append((pid, started))
        return pid != 14

    states = ta.read_claude_processes(sessions, alive)
    assert states == {"s-1": "waiting", "s-2": "idle"}
    assert (11, 134389435894606891) in seen
    assert ta.read_claude_processes(tmp_path / "nothing") == {}


def test_process_alive_for_current_process_and_bad_pids():
    import os

    assert ta.process_alive(os.getpid()) is True
    assert ta.process_alive(0) is False and ta.process_alive(-5) is False and ta.process_alive("x") is False and ta.process_alive(True) is False
    assert ta.process_alive(os.getpid(), 1) is (os.name != "nt")  # старт процесса не совпал (на Windows проверяется)


def test_unread_reader_returns_none_without_storage_and_ids_with_it(tmp_path):
    sessions = tmp_path / "Claude" / "claude-code-sessions"
    sessions.mkdir(parents=True)
    reader = ta.ClaudeUnreadReader()
    assert reader.read([sessions]) is None
    storage = tmp_path / "Claude" / "Local Storage" / "leveldb"
    storage.mkdir(parents=True)
    (storage / "000003.log").write_bytes(log_file([write_batch(9, [(1, KEY, unread_value(["local_a"]))])]))
    assert reader.read([sessions]) == {"local_a"}
    (storage / "000004.ldb").write_bytes(b"broken")
    assert reader.read([sessions]) is None  # неразобранное хранилище - «неизвестно», а не устаревшее значение


# ------------------------------- Codex ------------------------------------- #
def codex_databases(tmp_path, turns, receipts):
    history = sqlite3.connect(tmp_path / "thread_history_1.sqlite")
    history.execute("CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, status TEXT, started_at INTEGER)")
    history.executemany("INSERT INTO thread_turns VALUES (?,?,?,?)", turns)
    history.commit()
    history.close()
    state = sqlite3.connect(tmp_path / "state_5.sqlite")
    state.execute("CREATE TABLE thread_read_receipts (thread_id TEXT PRIMARY KEY, first_unread_turn TEXT, revision TEXT, last_published_turn TEXT)")
    state.executemany("INSERT INTO thread_read_receipts VALUES (?,?,?,?)", receipts)
    state.commit()
    state.close()
    return tmp_path / "thread_history_1.sqlite", tmp_path / "state_5.sqlite"


def test_codex_last_status_and_unread_flags(tmp_path):
    history, state = codex_databases(
        tmp_path,
        [("a", "t1", "completed", 10), ("a", "t2", "inProgress", 20), ("b", "t1", "inProgress", 5), ("b", "t2", "completed", 9)],
        [("a", None, "1", "t2"), ("b", "t2", "1", "t2"), ("c", "", "1", None)],
    )
    assert ta.read_codex_last_status(history, ["a", "b", "z"]) == {"a": "inProgress", "b": "completed"}
    assert ta.read_codex_unread(state, ["a", "b", "c", "z"]) == {"b"}
    assert ta.read_codex_last_status(history, []) == {} and ta.read_codex_unread(state, []) == set()


def test_codex_activity_needs_fresh_events_for_working():
    assert ta.codex_activity("inProgress", False, 30) == ta.ACTIVITY_WORKING
    assert ta.codex_activity("inProgress", True, 30) == ta.ACTIVITY_WORKING
    assert ta.codex_activity("inProgress", False, ta.CODEX_WORKING_FRESH_SECONDS + 1) == ""  # оборванный ход
    assert ta.codex_activity("inProgress", True, ta.CODEX_WORKING_FRESH_SECONDS + 1) == ta.ACTIVITY_UNREAD
    assert ta.codex_activity("completed", True, 5) == ta.ACTIVITY_UNREAD
    assert ta.codex_activity("completed", False, 5) == "" and ta.codex_activity(None, False, 5) == ""
    assert ta.ACTIVITY_NEEDS_REPLY not in {ta.codex_activity(s, u, 5) for s in ("inProgress", "completed", None) for u in (True, False)}


# -------------------------------- Grok ------------------------------------- #
def grok_line(kind, **fields):
    return json.dumps({"ts": "2026-11-12T10:00:00Z", "type": kind, **fields})


def state_of(lines):
    return ta._grok_state_from_lines(lines)


def test_grok_turn_states():
    started = grok_line("turn_started", session_id="g")
    assert state_of([started, grok_line("phase_changed", phase="streaming_text")]) == ta.ACTIVITY_WORKING
    assert state_of([started, grok_line("turn_ended", outcome="completed")]) == ""
    assert state_of([started, grok_line("turn_ended", outcome="cancelled"), grok_line("turn_started")]) == ta.ACTIVITY_WORKING
    pending = [started, grok_line("tool_started", tool_name="run_command"), grok_line("permission_requested", tool_name="run_command")]
    assert state_of(pending) == ta.ACTIVITY_NEEDS_REPLY
    assert state_of(pending + [grok_line("permission_resolved", tool_name="run_command", decision="allow", wait_ms=9000)]) == ta.ACTIVITY_WORKING
    asking = [started, grok_line("tool_started", tool_name="ask_user_question"), grok_line("permission_requested", tool_name="ask_user_question"), grok_line("permission_resolved", tool_name="ask_user_question", decision="allow", wait_ms=0)]
    assert state_of(asking) == ta.ACTIVITY_NEEDS_REPLY
    assert state_of(asking + [grok_line("tool_completed", tool_name="ask_user_question", tool_call_id="c-1")]) == ta.ACTIVITY_WORKING
    assert state_of(["not json", "[1, 2]", started]) == ta.ACTIVITY_WORKING
    assert state_of([grok_line("phase_changed")]) is None  # границ хода в окне нет: нужно читать глубже


def test_grok_turn_state_reads_deeper_when_turn_started_far_before_the_tail(tmp_path):
    path = tmp_path / "events.jsonl"
    filler = grok_line("phase_changed", phase="streaming_reasoning") + "\n"
    path.write_text(grok_line("turn_started") + "\n" + filler * 6000 + grok_line("permission_requested", tool_name="x") + "\n", encoding="utf-8")
    assert path.stat().st_size > ta.GROK_TAIL_BYTES
    assert ta.grok_turn_state(path) == ta.ACTIVITY_NEEDS_REPLY
    path.write_text(filler * 10, encoding="utf-8")
    assert ta.grok_turn_state(path) == ""
    assert ta.grok_turn_state(tmp_path / "absent.jsonl") == ""


def test_read_grok_active_keeps_only_live_sessions(tmp_path):
    (tmp_path / "active_sessions.json").write_text(json.dumps([
        {"session_id": "g-1", "pid": 10}, {"session_id": "g-2", "pid": 20}, {"pid": 30}, "junk",
    ]), encoding="utf-8")
    assert ta.read_grok_active(tmp_path, lambda pid, started: pid == 10) == {"g-1"}
    assert ta.read_grok_active(tmp_path / "none") == set()


# ------------------------ Сканер: состояния в тредах ------------------------ #
def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")


def claude_log(root, session, entrypoint="claude-desktop"):
    usage = {"input_tokens": 3, "output_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 3000,
             "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 3000}}
    write_jsonl(root / "projects" / "proj" / f"{session}.jsonl", [
        {"type": "assistant", "timestamp": iso(-100), "sessionId": session, "cwd": r"D:\Work\proj",
         "entrypoint": entrypoint, "isSidechain": False, "message": {"id": "m1", "role": "assistant", "usage": usage}},
    ])


def test_scanner_marks_claude_threads_from_process_status_and_desktop_unread(tmp_path):
    claude = tmp_path / "claude"
    desktop = tmp_path / "Claude" / DESKTOP_DIR
    for session in ("busy-1", "wait-1", "unread-1", "idle-1"):
        claude_log(claude, session)
    claude_log(claude, "cli-1", entrypoint="cli")
    (desktop / "org" / "acct").mkdir(parents=True)
    for session, host in (("busy-1", "local_b"), ("wait-1", "local_w"), ("unread-1", "local_u"), ("idle-1", "local_i")):
        (desktop / "org" / "acct" / f"{host}.json").write_text(json.dumps({"cliSessionId": session, "isArchived": False, "title": session}), encoding="utf-8")
    sessions = claude / "sessions"
    write_process(sessions, 1, "busy-1", "busy")
    write_process(sessions, 2, "wait-1", "waiting")
    write_process(sessions, 3, "idle-1", "idle")
    write_process(sessions, 4, "cli-1", "busy")
    storage = tmp_path / "Claude" / "Local Storage" / "leveldb"
    storage.mkdir(parents=True)
    (storage / "000003.log").write_bytes(log_file([write_batch(9, [(1, KEY, unread_value(["local_u", "local_i", "local_gone"]))])]))
    scanner = ThreadScanner(claude_dir=claude, codex_dir=tmp_path / "codex", desktop_dirs=[desktop], alive=lambda pid, started: True)
    states = {t.session_id: t.activity for t in scanner.scan(NOW)["claude"]}
    assert states == {
        "busy-1": ta.ACTIVITY_WORKING, "wait-1": ta.ACTIVITY_NEEDS_REPLY, "unread-1": ta.ACTIVITY_UNREAD,
        "idle-1": ta.ACTIVITY_UNREAD, "cli-1": ta.ACTIVITY_WORKING,
    }
    # Владелец открыл тред: идентификатор исчез из списка непросмотренных
    (storage / "000003.log").write_bytes(log_file([write_batch(9, [(1, KEY, unread_value(["local_u", "local_i"]))]), write_batch(11, [(1, KEY, unread_value(["local_u"]))])]))
    states = {t.session_id: t.activity for t in scanner.scan(NOW)["claude"]}
    assert states["idle-1"] == "" and states["unread-1"] == ta.ACTIVITY_UNREAD



def test_scanner_ignores_dead_processes_and_survives_broken_storage(tmp_path):
    claude = tmp_path / "claude"
    desktop = tmp_path / "Claude" / DESKTOP_DIR
    claude_log(claude, "s-1")
    (desktop / "org" / "acct").mkdir(parents=True)
    (desktop / "org" / "acct" / "local_x.json").write_text(json.dumps({"cliSessionId": "s-1", "isArchived": False, "title": "t"}), encoding="utf-8")
    write_process(claude / "sessions", 1, "s-1", "busy")
    storage = tmp_path / "Claude" / "Local Storage" / "leveldb"
    storage.mkdir(parents=True)
    (storage / "000009.ldb").write_bytes(b"garbage")
    scanner = ThreadScanner(claude_dir=claude, codex_dir=tmp_path / "codex", desktop_dirs=[desktop], alive=lambda pid, started: False)
    threads = scanner.scan(NOW)["claude"]
    assert [t.activity for t in threads] == [""]  # процесс не жив, хранилище не разобрано: состояния нет, таймер есть
    scanner = ThreadScanner(claude_dir=claude, codex_dir=tmp_path / "codex", desktop_dirs=[desktop], alive=lambda pid, started: True)
    assert [t.activity for t in scanner.scan(NOW)["claude"]] == [ta.ACTIVITY_WORKING]


def test_scanner_activity_failure_keeps_the_timers(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    claude_log(claude, "s-1")
    scanner = ThreadScanner(claude_dir=claude, codex_dir=tmp_path / "codex")

    def broken(*args, **kwargs):
        raise RuntimeError("сбой чтения")

    monkeypatch.setattr("claude_usage_manager.cache_threads.read_claude_processes", broken)
    threads = scanner.scan(NOW)["claude"]
    assert [(t.session_id, t.activity) for t in threads] == [("s-1", "")]
    assert scanner.scan(NOW)["claude"][0].session_id == "s-1"


def codex_log(codex_dir, session, last_offset=-60):
    stamp = (NOW + dt.timedelta(seconds=last_offset)).isoformat().replace("+00:00", "Z")
    write_jsonl(codex_dir / "2026" / "11" / "12" / f"rollout-{session}.jsonl", [
        {"timestamp": iso(-900), "type": "session_meta", "payload": {"id": session, "session_id": session, "cwd": r"D:\Work\proj", "originator": "Codex Desktop", "source": "vscode"}},
        {"timestamp": stamp, "type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 5000, "cached_input_tokens": 4000, "output_tokens": 50}}}},
    ])


def test_scanner_marks_codex_threads_from_history_and_read_receipts(tmp_path):
    codex = tmp_path / "codex"
    for session, offset in (("cx-run", -30), ("cx-unread", -300), ("cx-old", -1200), ("cx-read", -100)):
        codex_log(codex, session, offset)
    codex_databases(
        tmp_path,
        [("cx-run", "t", "inProgress", 5), ("cx-unread", "t", "completed", 5), ("cx-old", "t", "inProgress", 5), ("cx-read", "t", "completed", 5)],
        [("cx-unread", "t", "1", "t"), ("cx-read", None, "1", "t")],
    )
    state = sqlite3.connect(tmp_path / "state_5.sqlite")
    state.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT, archived INTEGER, updated_at INTEGER)")
    state.commit()
    state.close()
    claude = tmp_path / "claude"
    (claude / "projects").mkdir(parents=True)
    scanner = ThreadScanner(claude_dir=claude, codex_dir=codex, codex_ttl=3600)
    states = {t.session_id: t.activity for t in scanner.scan(NOW)["codex"]}
    assert states == {"cx-run": ta.ACTIVITY_WORKING, "cx-unread": ta.ACTIVITY_UNREAD, "cx-old": "", "cx-read": ""}


def test_scanner_marks_grok_threads_from_open_turn_and_live_process(tmp_path):
    grok = tmp_path / "grok"
    events = [
        {"ts": iso(-30), "src": "shell", "lvl": "info", "sid": sid, "msg": "shell.turn.inference_done",
         "ctx": {"prompt_tokens": 50000, "cached_prompt_tokens": 48000, "completion_tokens": 30}}
        for sid in ("g-run", "g-ask", "g-done", "g-dead")
    ]
    write_jsonl(grok / "logs" / "unified.jsonl", events)
    turns = {
        "g-run": [grok_line("turn_started"), grok_line("phase_changed")],
        "g-ask": [grok_line("turn_started"), grok_line("tool_started", tool_name="ask_user_question")],
        "g-done": [grok_line("turn_started"), grok_line("turn_ended", outcome="completed")],
        "g-dead": [grok_line("turn_started")],
    }
    for sid, lines in turns.items():
        folder = grok / "sessions" / "D%3A%5CWork" / sid
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text(json.dumps({"info": {"id": sid, "cwd": r"D:\Work"}, "generated_title": sid}), encoding="utf-8")
        (folder / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (grok / "active_sessions.json").write_text(json.dumps([
        {"session_id": sid, "pid": 100 + index} for index, sid in enumerate(("g-run", "g-ask", "g-done"))
    ] + [{"session_id": "g-dead", "pid": 999}]), encoding="utf-8")
    claude = tmp_path / "claude"
    (claude / "projects").mkdir(parents=True)
    scanner = ThreadScanner(claude_dir=claude, codex_dir=tmp_path / "codex", grok_dir=grok, alive=lambda pid, started: pid != 999)
    states = {t.session_id: t.activity for t in scanner.scan(NOW)["grok"]}
    assert states == {"g-run": ta.ACTIVITY_WORKING, "g-ask": ta.ACTIVITY_NEEDS_REPLY, "g-done": "", "g-dead": ""}
    # Ход закончился: файл изменился, состояние пересчитано
    folder = grok / "sessions" / "D%3A%5CWork" / "g-run"
    with (folder / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(grok_line("turn_ended", outcome="completed") + "\n")
    assert {t.session_id: t.activity for t in scanner.scan(NOW)["grok"]}["g-run"] == ""


# ------------------ Claude: итог хода просит ответа (blocked) ---------------- #
def wrap_up(blocked=None, ask="Что делаем дальше?", tool_id="tu-1"):
    arguments = {"recap": "Готово."}
    if ask:
        arguments.update({"ask": ask, "actions": [{"label": "Да"}]})
    if blocked is not None:
        arguments["blocked"] = blocked
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": "mcp__ccd_turn__wrap_up", "input": arguments}]}}


def tool_result(tool_id="tu-1"):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "Shown"}]}}


def closing_text():
    return {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Итог."}]}}


def owner_text(text="Да, делай"):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def other_tool():
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "tu-2", "name": "Bash", "input": {}}]}}


def blocked_of(tmp_path, records, name="j.jsonl"):
    path = tmp_path / name
    write_jsonl(path, records)
    return ta.claude_turn_blocked(path)


def test_claude_turn_blocked_follows_the_last_wrap_up_flag(tmp_path):
    ending = [tool_result(), closing_text()]
    assert blocked_of(tmp_path, [owner_text("вопрос"), wrap_up(True)] + ending) is True
    assert blocked_of(tmp_path, [owner_text("вопрос"), wrap_up(None)] + ending) is False  # вопрос есть, работа не встала
    assert blocked_of(tmp_path, [owner_text("вопрос"), wrap_up(False, ask="")] + ending) is False
    assert blocked_of(tmp_path, [wrap_up(True)] + ending + [owner_text()]) is False  # владелец ответил
    assert blocked_of(tmp_path, [wrap_up(True)] + ending + [{"type": "user", "message": {"role": "user", "content": "ответ строкой"}}]) is False
    assert blocked_of(tmp_path, [wrap_up(True), tool_result(), other_tool()]) is False  # агент продолжил работу
    assert blocked_of(tmp_path, [owner_text(), closing_text()]) is False
    assert blocked_of(tmp_path, [wrap_up(True)] + ending + [owner_text(), wrap_up(True, tool_id="tu-3"), tool_result("tu-3"), closing_text()]) is True
    assert blocked_of(tmp_path, [wrap_up(True)] + ending + [owner_text(), wrap_up(None, tool_id="tu-3"), tool_result("tu-3"), closing_text()]) is False
    assert blocked_of(tmp_path, []) is False
    assert ta.claude_turn_blocked(tmp_path / "нет-такого.jsonl") is False


def test_claude_turn_blocked_ignores_noise_and_reads_a_deeper_tail(tmp_path):
    noise = [{"type": "attachment", "attachment": {"type": "x" * 900}}] * 600  # около 600 КБ после итога хода
    records = [owner_text(), wrap_up(True), tool_result(), closing_text()] + noise
    path = tmp_path / "deep.jsonl"
    write_jsonl(path, records)
    assert path.stat().st_size > 2 * ta.CLAUDE_TURN_TAIL_BYTES
    assert ta.claude_turn_blocked(path) is True
    with path.open("a", encoding="utf-8") as handle:
        handle.write("не json\n[1, 2]\n" + json.dumps(owner_text()) + "\n")
    assert ta.claude_turn_blocked(path) is False
    broken = tmp_path / "broken.jsonl"
    broken.write_text("{оборванная первая строка\n" + json.dumps(wrap_up(True)) + "\n" + json.dumps(tool_result()) + "\n", encoding="utf-8")
    assert ta.claude_turn_blocked(broken) is True


def append_records(path, records):
    with path.open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def test_scanner_marks_claude_thread_needs_reply_from_blocked_wrap_up(tmp_path):
    claude = tmp_path / "claude"
    desktop = tmp_path / "Claude" / DESKTOP_DIR
    sessions = ("blk-1", "ask-1", "ans-1", "none-1")
    for session in sessions:
        claude_log(claude, session)
    journals = {session: claude / "projects" / "proj" / f"{session}.jsonl" for session in sessions}
    append_records(journals["blk-1"], [owner_text("вопрос"), wrap_up(True), tool_result(), closing_text()])
    append_records(journals["ask-1"], [owner_text("вопрос"), wrap_up(None), tool_result(), closing_text()])
    append_records(journals["ans-1"], [owner_text("вопрос"), wrap_up(True), tool_result(), closing_text(), owner_text("ответ")])
    append_records(journals["none-1"], [owner_text("вопрос"), closing_text()])
    (desktop / "org" / "acct").mkdir(parents=True)
    for session in sessions:
        (desktop / "org" / "acct" / f"local_{session}.json").write_text(json.dumps({"cliSessionId": session, "isArchived": False, "title": session}), encoding="utf-8")
    for pid, session in enumerate(sessions, start=1):
        write_process(claude / "sessions", pid, session, "idle")
    storage = tmp_path / "Claude" / "Local Storage" / "leveldb"
    storage.mkdir(parents=True)
    unread = [f"local_{session}" for session in sessions]  # непросмотрены все: ждет ответа важнее синей точки
    (storage / "000003.log").write_bytes(log_file([write_batch(9, [(1, KEY, unread_value(unread))])]))
    scanner = ThreadScanner(claude_dir=claude, codex_dir=tmp_path / "codex", desktop_dirs=[desktop], alive=lambda pid, started: True)
    states = {t.session_id: t.activity for t in scanner.scan(NOW)["claude"]}
    assert states == {"blk-1": ta.ACTIVITY_NEEDS_REPLY, "ask-1": ta.ACTIVITY_UNREAD, "ans-1": ta.ACTIVITY_UNREAD, "none-1": ta.ACTIVITY_UNREAD}
    # Владелец ответил в блокированном треде: журнал вырос, признак снят без перезапуска сканера
    append_records(journals["blk-1"], [owner_text("ответил")])
    states = {t.session_id: t.activity for t in scanner.scan(NOW)["claude"]}
    assert states["blk-1"] == ta.ACTIVITY_UNREAD
    # Следующий итог хода снова просит ответа
    append_records(journals["blk-1"], [wrap_up(True, tool_id="tu-9"), tool_result("tu-9"), closing_text()])
    assert {t.session_id: t.activity for t in scanner.scan(NOW)["claude"]}["blk-1"] == ta.ACTIVITY_NEEDS_REPLY
