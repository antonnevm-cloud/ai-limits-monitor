"""Read-only parser for Google Antigravity local conversation data."""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from collections import defaultdict
from typing import Any

from . import provider_settings
from .cache_threads import ANTIGRAVITY_DB_NAME, _read_antigravity, parse_iso
from .models import Period, ThreadRow
from .utils import clean_prompt, content_text, human_project, shorten

_MODEL_RE = re.compile(
    rb"(?:Gemini [0-9][A-Za-z0-9. ()/+_-]{2,80}|Claude [A-Za-z0-9. ()/+_-]{2,80}|GPT-OSS [A-Za-z0-9. ()/+_-]{2,80})"
)
_WORKSPACE_RE = re.compile(
    r"((?:[A-Za-z]:[\\/]|/)[^\x00-\x1f\[\]]{1,200}?)\s*->\s*[^\r\n]+",
    re.IGNORECASE,
)


def antigravity_root() -> pathlib.Path:
    return provider_settings.provider_profile("antigravity")


def _varint(buf: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(buf) and shift < 70:
        byte = buf[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid protobuf varint")


def _parse_fields(buf: bytes) -> list[tuple[int, int, Any]]:
    offset = 0
    fields: list[tuple[int, int, Any]] = []
    while offset < len(buf):
        key, offset = _varint(buf, offset)
        number, wire = key >> 3, key & 7
        if number <= 0 or wire not in (0, 1, 2, 5):
            raise ValueError("unsupported protobuf field")
        if wire == 0:
            value, offset = _varint(buf, offset)
        elif wire == 1:
            if offset + 8 > len(buf):
                raise ValueError("truncated fixed64")
            value = buf[offset : offset + 8]
            offset += 8
        elif wire == 5:
            if offset + 4 > len(buf):
                raise ValueError("truncated fixed32")
            value = buf[offset : offset + 4]
            offset += 4
        else:
            length, offset = _varint(buf, offset)
            if length < 0 or offset + length > len(buf):
                raise ValueError("truncated bytes")
            value = buf[offset : offset + length]
            offset += length
        fields.append((number, wire, value))
    return fields


def _try_fields(buf: bytes) -> list[tuple[int, int, Any]] | None:
    try:
        fields = _parse_fields(buf)
        return fields or None
    except (ValueError, IndexError):
        return None


def _usage_messages(blob: bytes) -> list[dict[str, int]]:
    """Find unique embedded exa.codeium_common_pb.ModelUsageStats messages.

    Field mapping was verified against the protobuf descriptors embedded in the
    installed Antigravity language_server.exe:
      2=input, 3=output, 4=cache_write, 5=cache_read,
      9=thinking_output, 10=response_output.
    """
    found: dict[tuple[int, ...], dict[str, int]] = {}

    def scan(buf: bytes, depth: int = 0) -> None:
        if depth > 9:
            return
        fields = _try_fields(buf)
        if not fields:
            return
        scalars: dict[int, int] = {}
        byte_fields: set[int] = set()
        for number, wire, value in fields:
            if wire == 0:
                scalars[number] = int(value)
            elif wire == 2:
                byte_fields.add(number)

        # ModelUsageStats in Antigravity generation metadata has a model enum
        # in field 1, input/output in 2/3, API provider in 6 and a session
        # reference in field 8. Optional zero-valued token fields are omitted.
        if (
            1 in scalars
            and 2 in scalars
            and 3 in scalars
            and 6 in scalars
            and 8 in byte_fields
            and any(number in scalars for number in (4, 5, 9, 10))
        ):
            record = {
                "model_enum": scalars.get(1, 0),
                "input": scalars.get(2, 0),
                "output": scalars.get(3, 0),
                "cache_write": scalars.get(4, 0),
                "cache_read": scalars.get(5, 0),
                "api_provider": scalars.get(6, 0),
                "thinking": scalars.get(9, 0),
                "response": scalars.get(10, 0),
            }
            key = tuple(record[name] for name in (
                "model_enum", "input", "output", "cache_write", "cache_read",
                "api_provider", "thinking", "response",
            ))
            found[key] = record

        for _, wire, value in fields:
            if wire == 2 and isinstance(value, bytes) and len(value) >= 2:
                # Very large prompt bodies are still protobuf containers and may
                # carry the final usage message, so they cannot be skipped.
                if _try_fields(value):
                    scan(value, depth + 1)

    scan(blob)
    return list(found.values())


def _model_name(blob: bytes) -> str:
    matches = [m.group().decode("ascii", "ignore").strip() for m in _MODEL_RE.finditer(blob)]
    if not matches:
        return "Antigravity"
    # The authoritative model display name is written at the end of generation
    # metadata; earlier matches may come from prompt/context text.
    return matches[-1].rstrip(" .-_/")


def _transcript_meta(path: pathlib.Path) -> tuple[str, list[Any], Any, Any]:
    title = ""
    responses = []
    first = None
    last = None
    if not path.exists():
        return title, responses, first, last
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                stamp = parse_iso(item.get("created_at"))
                if stamp is not None:
                    first = stamp if first is None or stamp < first else first
                    last = stamp if last is None or stamp > last else last
                kind = str(item.get("type") or "")
                if kind == "USER_INPUT" and not title:
                    prompt = clean_prompt(content_text(item.get("content")))
                    if prompt:
                        title = shorten(prompt)
                if kind == "PLANNER_RESPONSE" and stamp is not None:
                    responses.append(stamp)
    except OSError:
        return "", [], None, None
    return title, responses, first, last


def _workspace_from_blobs(blobs: list[bytes]) -> str:
    # Workspace URI is embedded in the generated context as
    #   d:\path\repo -> corpus/name
    text = b"\n".join(blobs).decode("utf-8", "ignore")
    matches = _WORKSPACE_RE.findall(text)
    for raw in matches:
        value = raw.strip().rstrip(" .;,:\t")
        if value and ".gemini/antigravity" not in value.casefold().replace("\\", "/"):
            return value
    return ""


def read_antigravity(period: Period) -> tuple[
    list[ThreadRow], dict[str, dict[str, Any]], list[dict[str, Any]], list[str]
]:
    if not provider_settings.provider_enabled("antigravity"):
        return [], {}, [], ["Antigravity отключен в настройках."]
    root = antigravity_root()
    conversations = root / "conversations"
    brain = root / "brain"
    warnings: list[str] = []
    rows: list[ThreadRow] = []
    model_acc: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "input": 0,
        "output": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "tokens": 0,
        "cost": 0.0,
        "session_ids": set(),
    })
    events: list[dict[str, Any]] = []
    summary_db = root / ANTIGRAVITY_DB_NAME
    summaries = {}
    if summary_db.is_file():
        try:
            summaries = {thread.session_id: thread for thread in _read_antigravity(summary_db, 0, limit=-1)}
        except (OSError, sqlite3.Error):
            pass

    for db_path in sorted(conversations.glob("*.db")):
        conversation_id = db_path.stem
        transcript = brain / conversation_id / ".system_generated" / "logs" / "transcript_full.jsonl"
        title, response_times, transcript_first, transcript_last = _transcript_meta(transcript)
        if not response_times:
            continue
        if response_times[-1] < period.start_utc or response_times[0] > period.end_utc:
            continue
        connection = None
        try:
            connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
            metadata_rows = connection.execute(
                "SELECT idx, data FROM gen_metadata ORDER BY idx"
            ).fetchall()
        except (OSError, sqlite3.Error):
            continue
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
        blobs = [bytes(data) for _, data in metadata_rows if isinstance(data, (bytes, bytearray))]
        project_path = _workspace_from_blobs(blobs)
        project = human_project(project_path, project_path) if project_path else "Antigravity"
        session_id = f"antigravity:{conversation_id}"

        total_input = total_output = cache_write = cache_read = total_tokens = 0
        models: set[str] = set()
        selected_first = None
        selected_last = None
        for position, (_, data) in enumerate(metadata_rows):
            if position >= len(response_times) or not isinstance(data, (bytes, bytearray)):
                continue
            stamp = response_times[position]
            if stamp < period.start_utc or stamp > period.end_utc:
                continue
            usages = _usage_messages(bytes(data))
            if not usages:
                continue
            model = _model_name(bytes(data))
            models.add(model)
            selected_first = stamp if selected_first is None or stamp < selected_first else selected_first
            selected_last = stamp if selected_last is None or stamp > selected_last else selected_last
            for usage in usages:
                inp = int(usage["input"])
                out = int(usage["output"])
                created = int(usage["cache_write"])
                cached = int(usage["cache_read"])
                tokens = inp + out + created + cached
                total_input += inp
                total_output += out
                cache_write += created
                cache_read += cached
                total_tokens += tokens
                acc = model_acc[model]
                acc["input"] += inp
                acc["output"] += out
                acc["cache_creation"] += created
                acc["cache_read"] += cached
                acc["tokens"] += tokens
                acc["session_ids"].add(session_id)
                events.append({
                    "session_id": session_id,
                    "timestamp": stamp,
                    "model": model,
                    "tokens": tokens,
                    "inputTokens": inp, "outputTokens": out, "cacheCreationTokens": created,
                    "cacheReadTokens": cached, "cost": None,
                })
        if total_tokens <= 0:
            continue
        summary = summaries.pop(conversation_id, None)
        rows.append(ThreadRow(
            session_id=session_id,
            title=summary.title if summary and not summary.title.startswith("Тред ") else title or f"Antigravity {conversation_id[:8]}",
            project=project if project_path or not summary else summary.project,
            agent="antigravity",
            project_path=project_path,
            models=sorted(models) or ["Antigravity"],
            cost=0.0,
            total_tokens=total_tokens,
            input_tokens=total_input,
            output_tokens=total_output,
            cache_creation=cache_write,
            cache_read=cache_read,
            first_activity=selected_first or transcript_first,
            last_activity=selected_last or transcript_last,
            member_ids=[session_id],
        ))

    if rows:
        warnings.append("Antigravity сообщает токены. Стоимость в журнале отсутствует.")
    for summary in summaries.values():
        if period.start_utc <= summary.started_at <= period.end_utc:
            key = f"antigravity:{summary.session_id}"
            rows.append(ThreadRow(
                session_id=key, title=summary.title, project=summary.project, agent="antigravity",
                last_activity=summary.started_at, member_ids=[key],
            ))
    if any(not row.total_tokens for row in rows):
        warnings.append("Для части сессий Antigravity доступны только название и время.")
    return rows, model_acc, events, warnings
