"""Чтение одного ключа из LevelDB-хранилища Chromium (Local Storage приложения), только чтение.

Нужен для одного флага: список непросмотренных тредов Claude Desktop лежит в
Local Storage приложения, отдельного файла или API у него нет. Читается только
запрошенный ключ: из таблиц разбираются блоки, где он может лежать, значения
остальных ключей не сохраняются и не выводятся. Файлы не изменяются; формат -
LevelDB (журнал `.log` и таблицы `.ldb` со сжатием Snappy), побеждает запись с
наибольшим порядковым номером. Любая неожиданность в формате - `LevelDbError`,
вызывающий считает данные неизвестными, а не подставляет устаревшее значение.
"""
from __future__ import annotations

import pathlib
import struct

LOG_BLOCK_SIZE = 32768
LOG_HEADER_SIZE = 7
TABLE_FOOTER_SIZE = 48
TABLE_MAGIC = bytes.fromhex("57fb808b247547db")
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_BLOCK_BYTES = 64 * 1024 * 1024
TYPE_DELETION = 0
TYPE_VALUE = 1
TABLE_SUFFIXES = (".ldb", ".sst")
LOG_SUFFIX = ".log"


class LevelDbError(Exception):
    """Хранилище не разобрано: формат или чтение файла не соответствуют ожидаемым."""


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        if pos >= len(data):
            raise LevelDbError("обрыв числа переменной длины")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise LevelDbError("слишком длинное число")


def snappy_decompress(data: bytes) -> bytes:
    """Распаковка Snappy (формат без обрамления), как у блоков таблиц LevelDB."""
    length, pos = _varint(data, 0)
    if length > MAX_BLOCK_BYTES:
        raise LevelDbError("блок слишком велик")
    out = bytearray()
    size_in = len(data)
    while pos < size_in:
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:
            size = tag >> 2
            if size >= 60:
                extra = size - 59
                if pos + extra > size_in:
                    raise LevelDbError("обрыв длины литерала")
                size = int.from_bytes(data[pos:pos + extra], "little")
                pos += extra
            size += 1
            if pos + size > size_in:
                raise LevelDbError("обрыв литерала")
            out += data[pos:pos + size]
            pos += size
            continue
        if kind == 1:
            if pos >= size_in:
                raise LevelDbError("обрыв копии")
            size = 4 + ((tag >> 2) & 7)
            offset = ((tag >> 5) << 8) | data[pos]
            pos += 1
        elif kind == 2:
            if pos + 2 > size_in:
                raise LevelDbError("обрыв копии")
            size = 1 + (tag >> 2)
            offset = int.from_bytes(data[pos:pos + 2], "little")
            pos += 2
        else:
            if pos + 4 > size_in:
                raise LevelDbError("обрыв копии")
            size = 1 + (tag >> 2)
            offset = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        if offset == 0 or offset > len(out):
            raise LevelDbError("неверное смещение копии")
        start = len(out) - offset
        if offset >= size:
            out += out[start:start + size]
        else:
            for index in range(size):
                out.append(out[start + index])
        if len(out) > MAX_BLOCK_BYTES:
            raise LevelDbError("блок слишком велик")
    if len(out) != length:
        raise LevelDbError("длина после распаковки не совпала")
    return bytes(out)


def _block_entries(block: bytes):
    """Записи блока таблицы: (ключ, значение); ключи хранятся с общим началом."""
    if len(block) < 4:
        raise LevelDbError("короткий блок")
    restarts = struct.unpack_from("<I", block, len(block) - 4)[0]
    end = len(block) - 4 - 4 * restarts
    if end < 0:
        raise LevelDbError("неверный список перезапусков")
    pos = 0
    key = b""
    while pos < end:
        shared, pos = _varint(block, pos)
        non_shared, pos = _varint(block, pos)
        value_size, pos = _varint(block, pos)
        if shared > len(key) or pos + non_shared + value_size > end:
            raise LevelDbError("неверная запись блока")
        key = key[:shared] + block[pos:pos + non_shared]
        pos += non_shared
        value = block[pos:pos + value_size]
        pos += value_size
        yield key, value


def _read_block(data: bytes, offset: int, size: int) -> bytes:
    if offset < 0 or offset + size + 1 > len(data):
        raise LevelDbError("блок за пределами файла")
    raw = data[offset:offset + size]
    compression = data[offset + size]
    if compression == 0:
        return raw
    if compression == 1:
        return snappy_decompress(raw)
    raise LevelDbError("неизвестное сжатие блока")


def _table_versions(data: bytes, wanted: bytes) -> list[tuple[int, bytes | None]]:
    """Версии ключа в таблице: (порядковый номер, значение или None для удаления)."""
    if len(data) < TABLE_FOOTER_SIZE or data[-8:] != TABLE_MAGIC:
        raise LevelDbError("нет подписи таблицы")
    footer = data[-TABLE_FOOTER_SIZE:]
    pos = 0
    _, pos = _varint(footer, pos)  # метаиндекс: смещение
    _, pos = _varint(footer, pos)  # метаиндекс: размер
    index_offset, pos = _varint(footer, pos)
    index_size, pos = _varint(footer, pos)
    index = _read_block(data, index_offset, index_size)
    found: list[tuple[int, bytes | None]] = []
    for separator, handle in _block_entries(index):
        if len(separator) < 8:
            raise LevelDbError("короткий ключ индекса")
        last_user_key = separator[:-8]
        if last_user_key < wanted:
            continue
        block_offset, hpos = _varint(handle, 0)
        block_size, _ = _varint(handle, hpos)
        for key, value in _block_entries(_read_block(data, block_offset, block_size)):
            if len(key) < 8 or key[:-8] != wanted:
                continue
            packed = int.from_bytes(key[-8:], "little")
            kind = packed & 0xFF
            if kind not in (TYPE_VALUE, TYPE_DELETION):
                raise LevelDbError("неизвестный тип записи")
            found.append((packed >> 8, value if kind == TYPE_VALUE else None))
        if last_user_key > wanted:
            break
    return found


def _log_batches(data: bytes):
    """Пакеты записи из журнала: склеивает фрагменты логических записей."""
    pos = 0
    fragment = bytearray()
    total = len(data)
    while pos + LOG_HEADER_SIZE <= total:
        room = LOG_BLOCK_SIZE - (pos % LOG_BLOCK_SIZE)
        if room < LOG_HEADER_SIZE:
            pos += room
            continue
        size = int.from_bytes(data[pos + 4:pos + 6], "little")
        kind = data[pos + 6]
        pos += LOG_HEADER_SIZE
        if kind == 0:
            pos += room - LOG_HEADER_SIZE
            continue
        if size > room - LOG_HEADER_SIZE or pos + size > total:
            break  # запись дописывается прямо сейчас: неполный хвост не учитывается
        chunk = data[pos:pos + size]
        pos += size
        if kind == 1:
            yield bytes(chunk)
        elif kind == 2:
            fragment = bytearray(chunk)
        elif kind == 3:
            fragment += chunk
        elif kind == 4:
            fragment += chunk
            yield bytes(fragment)
            fragment = bytearray()
        else:
            raise LevelDbError("неизвестный тип записи журнала")


def _log_versions(data: bytes, wanted: bytes) -> list[tuple[int, bytes | None]]:
    found: list[tuple[int, bytes | None]] = []
    for batch in _log_batches(data):
        if len(batch) < 12:
            raise LevelDbError("короткий пакет записи")
        sequence = int.from_bytes(batch[:8], "little")
        count = int.from_bytes(batch[8:12], "little")
        pos = 12
        for index in range(count):
            if pos >= len(batch):
                raise LevelDbError("обрыв пакета записи")
            tag = batch[pos]
            pos += 1
            key_size, pos = _varint(batch, pos)
            key = batch[pos:pos + key_size]
            pos += key_size
            if tag == TYPE_VALUE:
                value_size, pos = _varint(batch, pos)
                value = batch[pos:pos + value_size]
                pos += value_size
                if key == wanted:
                    found.append((sequence + index, bytes(value)))
            elif tag == TYPE_DELETION:
                if key == wanted:
                    found.append((sequence + index, None))
            else:
                raise LevelDbError("неизвестный тип записи пакета")
    return found


class LevelDbKeyReader:
    """Значение одного ключа из каталога LevelDB; файл разбирается заново, только если изменился."""

    def __init__(self, key: bytes) -> None:
        self._key = key
        self._cache: dict[str, tuple[int, int, list[tuple[int, bytes | None]]]] = {}

    def read(self, directory: pathlib.Path) -> bytes | None:
        """Последнее значение ключа; None, если ключа нет или он удален. Ошибка формата - `LevelDbError`."""
        try:
            files = [p for p in directory.iterdir() if p.suffix in TABLE_SUFFIXES + (LOG_SUFFIX,)]
        except OSError as error:
            raise LevelDbError(f"каталог недоступен: {error}") from error
        seen: set[str] = set()
        versions: list[tuple[int, bytes | None]] = []
        for path in files:
            try:
                stat = path.stat()
            except OSError as error:
                raise LevelDbError(f"файл недоступен: {error}") from error
            if stat.st_size > MAX_FILE_BYTES:
                raise LevelDbError("файл слишком велик")
            key = str(path)
            seen.add(key)
            cached = self._cache.get(key)
            if cached is None or cached[:2] != (stat.st_mtime_ns, stat.st_size):
                try:
                    data = path.read_bytes()
                except OSError as error:
                    raise LevelDbError(f"файл не прочитан: {error}") from error
                found = _log_versions(data, self._key) if path.suffix == LOG_SUFFIX else _table_versions(data, self._key)
                cached = (stat.st_mtime_ns, stat.st_size, found)
                self._cache[key] = cached
            versions.extend(cached[2])
        for key in [k for k in self._cache if k not in seen]:
            del self._cache[key]
        if not versions:
            return None
        return max(versions, key=lambda item: item[0])[1]
