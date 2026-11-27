"""Построчное чтение журналов без загрузки крупных полей диалога в память."""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any, BinaryIO

CHUNK_BYTES = 256 * 1024
_SCALAR_BYTES = 16 * 1024
_TOKEN = re.compile(rb'\s+|[{}\[\]:,"]|[^{}\[\]:,"\s]+')
_STRING_BREAK = re.compile(rb'["\\\x00-\x1f]')
_ATOM_END = re.compile(rb'[{}\[\]:,"\s]')
_PUNCTUATION = {b"{", b"}", b"[", b"]", b":", b","}
_END = (b"", None)

CLAUDE_USAGE_FIELDS = {
    "type": None, "timestamp": None, "cwd": None, "isSidechain": None, "isApiErrorMessage": None,
    "message": {"id": None, "model": None, "usage": {
        "input_tokens": None, "output_tokens": None, "cache_read_input_tokens": None,
        "cache_creation_input_tokens": None,
        "cache_creation": {"ephemeral_5m_input_tokens": None, "ephemeral_1h_input_tokens": None},
    }},
}
_CODEX_USAGE = dict.fromkeys((
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens", "total_tokens",
))
CODEX_USAGE_FIELDS = {
    "type": None, "timestamp": None,
    "payload": {
        "model": None, "cwd": None, "session_id": None, "id": None, "turn_id": None,
        "response_id": None, "usage": _CODEX_USAGE,
        "info": {"last_token_usage": _CODEX_USAGE, "total_token_usage": _CODEX_USAGE},
    },
}
CODEX_MODEL_FIELDS = {"type": None, "payload": {"model": None}}


def jsonl_records(
    stream: BinaryIO, markers: tuple[bytes, ...], fields: dict[str, Any],
) -> Iterator[tuple[int, dict[str, Any] | None]]:
    """Возвращает смещение после полной строки и нужную для сканера запись."""
    offset = stream.tell()
    while data := stream.readline(CHUNK_BYTES):
        offset += len(data)
        if data.endswith(b"\n"):
            item = None
            if any(marker in data for marker in markers):
                try:
                    item = json.loads(data)
                except (ValueError, RecursionError):
                    pass
        elif len(data) == CHUNK_BYTES:
            complete = False

            def chunks():
                nonlocal offset, complete
                yield data.removeprefix(b"\xef\xbb\xbf")
                while chunk := stream.readline(CHUNK_BYTES):
                    offset += len(chunk)
                    yield chunk
                    if chunk.endswith(b"\n"):
                        complete = True
                        return

            parts = chunks()
            tokens = _tokens(parts)
            try:
                item = _value(next(tokens, _END), tokens, fields)
                if next(tokens, _END) != _END:
                    item = None
            except (ValueError, RecursionError):
                item = None
            for _ in parts:
                pass
            if not complete:
                return
        else:
            return
        yield offset, item if isinstance(item, dict) else None


class _Chunks:
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self.chunks = chunks
        self.data = b""
        self.pos = 0

    def ready(self) -> bool:
        if self.pos == len(self.data):
            self.data = next(self.chunks, b"")
            self.pos = 0
        return bool(self.data)

    def scalar(self, quoted: bool) -> bytes | None:
        raw = bytearray(b'"' if quoted else b"")
        pattern = _STRING_BREAK if quoted else _ATOM_END
        escape_tail = 0
        while self.ready():
            start = self.pos
            while escape_tail and self.pos < len(self.data):
                char = self.data[self.pos]
                self.pos += 1
                if escape_tail == -1:
                    if char not in b'"\\/bfnrtu':
                        raise ValueError("Недопустимое экранирование")
                    escape_tail = 4 if char == ord("u") else 0
                elif char in b"0123456789abcdefABCDEF":
                    escape_tail -= 1
                else:
                    raise ValueError("Недопустимое значение Unicode")
            match = pattern.search(self.data, self.pos)
            self.pos = match.start() if match else len(self.data)
            if quoted and match:
                if match[0] == b"\\":
                    escape_tail = -1
                elif match[0] != b'"':
                    raise ValueError("Управляющий символ в строке")
                self.pos += 1
            if raw is not None:
                if len(raw) + self.pos - start > _SCALAR_BYTES:
                    raw = None
                else:
                    raw.extend(memoryview(self.data)[start:self.pos])
            if match and not escape_tail:
                return bytes(raw) if raw is not None else None
        if quoted:
            raise ValueError("Незакрытая строка")
        return bytes(raw) if raw is not None else None


def _tokens(parts: Iterator[bytes]):
    stream = _Chunks(parts)
    while stream.ready():
        match = _TOKEN.match(stream.data, stream.pos)
        token = match[0]
        if token == b'"':
            stream.pos += 1
            yield b'"', stream.scalar(quoted=True)
        elif token in _PUNCTUATION:
            stream.pos = match.end()
            yield token, None
        elif token.isspace():
            stream.pos = match.end()
        else:
            yield b"scalar", stream.scalar(quoted=False)


def _value(token, tokens, fields):
    kind, raw = token
    if kind == b"{" and fields is not None:
        item = {}
        token = next(tokens, _END)
        while token[0] == b'"':
            key = json.loads(token[1]) if token[1] is not None else None
            if next(tokens, _END)[0] != b":":
                raise ValueError("Нет значения поля")
            token = next(tokens, _END)
            if key in fields:
                item[key] = _value(token, tokens, fields[key])
            else:
                _skip(token, tokens)
            token = next(tokens, _END)
            if token[0] != b",":
                break
            token = next(tokens, _END)
        if token[0] != b"}":
            raise ValueError("Незакрытый объект")
        return item
    if raw is not None:
        return json.loads(raw)
    _skip(token, tokens)
    return None


def _skip(token, tokens):
    kind, raw = token
    if kind in (b'"', b"scalar"):
        if raw is not None:
            json.loads(raw)
        return
    if kind not in (b"{", b"["):
        raise ValueError("Нет значения")
    end = b"}" if kind == b"{" else b"]"
    token = next(tokens, _END)
    if token[0] == end:
        return
    while True:
        if kind == b"{":
            if token[0] != b'"' or next(tokens, _END)[0] != b":":
                raise ValueError("Нет ключа объекта")
            token = next(tokens, _END)
        _skip(token, tokens)
        token = next(tokens, _END)
        if token[0] == end:
            return
        if token[0] != b",":
            raise ValueError("Незакрытое значение")
        token = next(tokens, _END)
