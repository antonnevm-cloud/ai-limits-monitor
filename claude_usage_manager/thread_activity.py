"""Состояние треда по локальным данным: работает, ждет ответа владельца, ход завершен и не просмотрен.

Только чтение. Что берется и откуда (проверено на живых данных 12.11.2026):

- Claude: `~/.claude/sessions/<pid>.json` - `status` процесса (`busy` работает, `waiting` ждет
  ответа или разрешения, `idle` ждет владельца; нужен живой процесс). Ждет ответа и у Desktop с
  простаивающим процессом: последний итог хода (`wrap_up`) в журнале `projects/*/<id>.jsonl`
  заявил `blocked: true` (так приложение рисует оранжевую точку со знаком вопроса), пока владелец
  не написал следующее сообщение. Признак «не просмотрен»
  только у Claude Desktop: идентификатор записи приложения в списке `epitaxy-unread-v1`
  Local Storage приложения (флаг, по которому приложение рисует синюю точку).
- Codex: `thread_history_*.sqlite`, таблица `thread_turns` (последний ход в статусе `inProgress`
  и недавняя активность - работает) и `state_*.sqlite`, таблица `thread_read_receipts`
  (`first_unread_turn` не пуст - не просмотрен). Признака «ждет ответа» в данных Codex нет.
- Grok Build: `sessions/*/*/events.jsonl` - открытый ход (`turn_started` без `turn_ended`) при живом
  процессе из `active_sessions.json` - работает; запрос разрешения без решения или вопрос
  `ask_user_question` без ответа - ждет ответа. Признака «просмотрен» у Grok нет.

Если признака нет или данные не разобраны, состояние пустое: ничего не придумывается.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
from typing import Any, Callable

from .chromium_leveldb import LevelDbError, LevelDbKeyReader

ACTIVITY_WORKING = "working"
ACTIVITY_NEEDS_REPLY = "needs_reply"
ACTIVITY_UNREAD = "unread"
ACTIVITIES = (ACTIVITY_WORKING, ACTIVITY_NEEDS_REPLY, ACTIVITY_UNREAD)

CODEX_WORKING_FRESH_SECONDS = 600
CLAUDE_TURN_TAIL_BYTES = 256 * 1024
CLAUDE_TURN_TAIL_MAX_BYTES = 16 * 1024 * 1024
CLAUDE_WRAP_UP_SUFFIX = "wrap_up"
GROK_TAIL_BYTES = 256 * 1024
GROK_TAIL_MAX_BYTES = 8 * 1024 * 1024
PROCESS_START_TOLERANCE = 50_000_000  # 5 секунд в сотнях наносекунд (FILETIME)
UNREAD_KEY = b"_https://claude.ai\x00\x01epitaxy-unread-v1"
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

AliveCheck = Callable[[int, int | None], bool]


# --------------------------------------------------------------------------- #
# Процессы
# --------------------------------------------------------------------------- #
def process_alive(pid: Any, started_filetime: int | None = None) -> bool:
    """Жив ли процесс; с `started_filetime` (Windows FILETIME) еще и сверяется время его старта (защита от повтора pid)."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return False
        if started_filetime:
            created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            if kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                value = (created.dwHighDateTime << 32) | created.dwLowDateTime
                if abs(value - started_filetime) > PROCESS_START_TOLERANCE:
                    return False
        return True
    finally:
        kernel.CloseHandle(handle)


def _load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Claude
# --------------------------------------------------------------------------- #
_STATUS_RANK = {"waiting": 2, "busy": 1, "idle": 0}


def read_claude_processes(sessions_dir: pathlib.Path, alive: AliveCheck = process_alive) -> dict[str, str]:
    """Статус живых процессов Claude по идентификатору сессии (имени журнала): waiting, busy или idle."""
    states: dict[str, str] = {}
    try:
        files = sorted(sessions_dir.glob("*.json"))
    except OSError:
        return states
    for path in files:
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        session_id, status, pid = data.get("sessionId"), data.get("status"), data.get("pid")
        if not isinstance(session_id, str) or not session_id or status not in _STATUS_RANK:
            continue
        try:
            started = int(str(data.get("procStart"))) if data.get("procStart") is not None else None
        except ValueError:
            started = None
        if not alive(pid, started):
            continue
        known = states.get(session_id)
        if known is None or _STATUS_RANK[status] > _STATUS_RANK[known]:
            states[session_id] = status
    return states


def parse_unread_ids(value: bytes | None) -> set[str]:
    """Идентификаторы записей Claude Desktop из значения `epitaxy-unread-v1` (первый байт - кодировка)."""
    if not value:
        return set()
    marker, body = value[0], value[1:]
    try:
        text = body.decode("latin-1") if marker == 1 else body.decode("utf-16-le")
        state = json.loads(text).get("state")
    except (ValueError, AttributeError):
        raise LevelDbError("значение списка непросмотренных не разобрано") from None
    if not isinstance(state, dict):
        raise LevelDbError("в значении нет списка непросмотренных")
    ids: set[str] = set()
    for field in ("unreadIds", "explicitUnreadIds"):
        items = state.get(field)
        if isinstance(items, list):
            ids.update(item for item in items if isinstance(item, str))
    return ids


def _blocked_by_last_record(record: dict[str, Any]) -> bool | None:
    """Решает по одной записи журнала Claude, идя с конца: True/False - итог, None - запись не важна."""
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    kind = record.get("type")
    if kind == "user":
        if isinstance(content, str):
            return False if content.strip() else None
        if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") != "tool_result" for block in content):
            return False  # владелец написал после запроса: ответ дан, ход идет
        return None
    if kind == "assistant" and isinstance(content, list):
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                if str(block.get("name", "")).endswith(CLAUDE_WRAP_UP_SUFFIX):
                    arguments = block.get("input")
                    return isinstance(arguments, dict) and arguments.get("blocked") is True
                return False  # после запроса ответа агент продолжил работу
    return None


def claude_turn_blocked(path: pathlib.Path) -> bool:
    """Ждет ли тред Claude Desktop ответа владельца: последний итог хода (`wrap_up`) заявил `blocked: true`.

    Это тот же признак, по которому приложение рисует оранжевую точку со знаком вопроса: агент в итоге хода
    сообщает, что работа встала до ответа. Ответ владельца (новая запись `user`) признак снимает. Читается
    хвост журнала, при нехватке расширяется до `CLAUDE_TURN_TAIL_MAX_BYTES`.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            window = CLAUDE_TURN_TAIL_BYTES
            while True:
                offset = max(0, size - window)
                handle.seek(offset)
                lines = handle.read().splitlines()
                if offset:
                    lines = lines[1:]  # первая строка хвоста может быть оборвана
                for raw in reversed(lines):
                    try:
                        record = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    verdict = _blocked_by_last_record(record)
                    if verdict is not None:
                        return verdict
                if offset == 0 or window >= CLAUDE_TURN_TAIL_MAX_BYTES:
                    return False
                window *= 4
    except OSError:
        return False


def claude_activity(status: str | None, unread: bool, blocked: bool = False) -> str:
    """Ждет ответа важнее всего, затем работает, затем не просмотрен (решение владельца 12.11.2026).

    Процесс Desktop остается `busy` и при фоновых задачах после конца хода: такой тред показывается работающим
    и синеет, когда процесс освободится.
    """
    if status == "waiting" or blocked:
        return ACTIVITY_NEEDS_REPLY
    if status == "busy":
        return ACTIVITY_WORKING
    if unread:
        return ACTIVITY_UNREAD
    return ""


class ClaudeUnreadReader:
    """Список непросмотренных тредов Claude Desktop из Local Storage приложения; None - не удалось прочитать."""

    def __init__(self) -> None:
        self._reader = LevelDbKeyReader(UNREAD_KEY)

    def read(self, directories: list[pathlib.Path]) -> set[str] | None:
        found: set[str] | None = None
        for directory in directories:
            storage = directory.parent / "Local Storage" / "leveldb"
            if not storage.is_dir():
                continue
            try:
                ids = parse_unread_ids(self._reader.read(storage))
            except LevelDbError:
                return None
            found = (found or set()) | ids
        return found


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #
def _open_readonly(path: pathlib.Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)


def newest_database(directory: pathlib.Path, pattern: str) -> pathlib.Path | None:
    try:
        found = sorted(directory.glob(pattern), key=lambda p: (len(p.stem), p.stem))
    except OSError:
        return None
    return found[-1] if found else None


def read_codex_last_status(history_db: pathlib.Path, thread_ids: list[str]) -> dict[str, str]:
    """Статус последнего хода каждого треда из `thread_turns` (completed, failed, interrupted, inProgress)."""
    statuses: dict[str, str] = {}
    if not thread_ids:
        return statuses
    connection = _open_readonly(history_db)
    try:
        for thread_id in thread_ids:
            row = connection.execute(
                "SELECT status FROM thread_turns WHERE thread_id = ? ORDER BY started_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            if row and isinstance(row[0], str):
                statuses[thread_id] = row[0]
    finally:
        connection.close()
    return statuses


def read_codex_unread(state_db: pathlib.Path, thread_ids: list[str]) -> set[str]:
    """Треды, у которых есть непросмотренный ход (`first_unread_turn` не пуст в `thread_read_receipts`)."""
    unread: set[str] = set()
    if not thread_ids:
        return unread
    connection = _open_readonly(state_db)
    try:
        for thread_id in thread_ids:
            row = connection.execute(
                "SELECT first_unread_turn FROM thread_read_receipts WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if row and row[0]:
                unread.add(thread_id)
    finally:
        connection.close()
    return unread


def codex_activity(last_status: str | None, unread: bool, idle_seconds: float) -> str:
    """Работает: последний ход `inProgress` и недавняя активность (оборванные ходы остаются `inProgress` навсегда)."""
    if last_status == "inProgress" and idle_seconds <= CODEX_WORKING_FRESH_SECONDS:
        return ACTIVITY_WORKING
    if unread:
        return ACTIVITY_UNREAD
    return ""


# --------------------------------------------------------------------------- #
# Grok Build
# --------------------------------------------------------------------------- #
def read_grok_active(grok_dir: pathlib.Path, alive: AliveCheck = process_alive) -> set[str]:
    """Идентификаторы сессий Grok с живым процессом (`active_sessions.json`)."""
    data = _load_json(grok_dir / "active_sessions.json")
    if not isinstance(data, list):
        return set()
    return {
        item["session_id"]
        for item in data
        if isinstance(item, dict) and isinstance(item.get("session_id"), str) and alive(item.get("pid"), None)
    }


def _tail_lines(path: pathlib.Path, size: int) -> tuple[list[str], bool]:
    """Последние `size` байт файла строками и признак, что это весь файл."""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        end = stream.tell()
        stream.seek(max(0, end - size))
        raw = stream.read(size)
    whole = end <= size
    if not whole:
        raw = raw.partition(b"\n")[2]
    return raw.decode("utf-8", errors="replace").splitlines(), whole


def grok_turn_state(events_path: pathlib.Path) -> str:
    """Состояние последнего хода сессии Grok по `events.jsonl`: working, needs_reply или пусто."""
    size = GROK_TAIL_BYTES
    while True:
        try:
            lines, whole = _tail_lines(events_path, size)
        except OSError:
            return ""
        state = _grok_state_from_lines(lines)
        if state is not None:
            return state
        if whole or size >= GROK_TAIL_MAX_BYTES:
            return ""
        size *= 4


def _grok_state_from_lines(lines: list[str]) -> str | None:
    """None, если в окне нет ни начала, ни конца хода (нужно читать глубже)."""
    open_turn: bool | None = None
    permission = question = False
    for line in lines:
        if '"turn_started"' not in line and '"turn_ended"' not in line and '"permission_' not in line and '"ask_user_question"' not in line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        kind = item.get("type") if isinstance(item, dict) else None
        if kind == "turn_started":
            open_turn, permission, question = True, False, False
        elif kind == "turn_ended":
            open_turn, permission, question = False, False, False
        elif kind == "permission_requested":
            permission = True
        elif kind == "permission_resolved":
            permission = False
        elif isinstance(item, dict) and item.get("tool_name") == "ask_user_question":
            if kind == "tool_started":
                question = True
            elif kind == "tool_completed":
                question = False
    if open_turn is None:
        return None
    if not open_turn:
        return ""
    return ACTIVITY_NEEDS_REPLY if permission or question else ACTIVITY_WORKING
