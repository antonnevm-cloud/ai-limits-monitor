"""Название треда в середине длинного журнала и кэш LevelDB нескольких каталогов."""
import json

from claude_usage_manager import chromium_leveldb
from claude_usage_manager.cache_threads import _scan_custom_titles


def test_custom_title_in_the_middle_of_a_long_journal_is_kept(tmp_path):
    journal = tmp_path / "s.jsonl"
    filler = json.dumps({"type": "assistant", "message": {"content": "x" * 2000}}) + "\n"
    with journal.open("w", encoding="utf-8") as stream:
        stream.write(filler * 200)
        stream.write(json.dumps({"type": "custom-title", "customTitle": "Мое название треда"}) + "\n")
        stream.write(filler * 200)
    offset, title = _scan_custom_titles(journal, 0, "")
    assert title == "Мое название треда"
    assert offset == journal.stat().st_size
    # Журнал растет дальше: дочитывается только новая часть, название сохраняется.
    with journal.open("a", encoding="utf-8") as stream:
        stream.write(filler * 300)
        stream.write('{"type": "user", "partial')  # недописанная строка не читается
    offset2, title2 = _scan_custom_titles(journal, offset, title)
    assert title2 == "Мое название треда"
    assert offset < offset2 < journal.stat().st_size


def test_leveldb_reader_keeps_cache_of_other_directories(tmp_path, monkeypatch):
    reads = []
    original = chromium_leveldb.pathlib.Path.read_bytes

    def counting(self):
        reads.append(self.name)
        return original(self)

    monkeypatch.setattr(chromium_leveldb.pathlib.Path, "read_bytes", counting)
    monkeypatch.setattr(chromium_leveldb, "_log_versions", lambda data, key: [])
    first, second = tmp_path / "a", tmp_path / "b"
    for folder in (first, second):
        folder.mkdir()
        (folder / "000001.log").write_bytes(b"data")
    reader = chromium_leveldb.LevelDbKeyReader(b"key")
    for _ in range(3):
        reader.read(first)
        reader.read(second)
    # Каждый файл разбирается один раз, а не на каждом скане.
    assert len(reads) == 2
