import pathlib
import datetime as dt
import sqlite3

from claude_usage_manager.antigravity import _transcript_meta, _usage_messages, _workspace_from_blobs


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _v(field, value):
    return _varint(field << 3) + _varint(value)


def _b(field, value):
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def test_antigravity_model_usage_stats_exact_fields_and_dedup():
    usage = b"".join([
        _v(1, 7), _v(2, 2268), _v(3, 936), _v(5, 24793), _v(6, 2),
        _b(8, b"session-id"), _v(9, 908), _v(10, 28),
    ])
    wrapper = _b(4, usage) + _b(7, usage)
    rows = _usage_messages(wrapper)
    assert len(rows) == 1
    row = rows[0]
    assert row["input"] == 2268
    assert row["output"] == 936
    assert row["cache_read"] == 24793
    assert row["cache_write"] == 0
    assert row["thinking"] == 908
    assert row["response"] == 28


def test_antigravity_workspace_mapping():
    blob = b"prefix c:\\Workspace\\sample-project -> corpus/demo suffix"
    assert _workspace_from_blobs([blob]).casefold().endswith("sample-project")


def test_antigravity_transcript_race_is_treated_as_missing(monkeypatch, tmp_path):
    path = tmp_path / "transcript_full.jsonl"
    path.write_text("{}\n", encoding="utf-8")

    def unavailable(*args, **kwargs):
        raise OSError("file disappeared")

    monkeypatch.setattr(pathlib.Path, "open", unavailable)

    assert _transcript_meta(path) == ("", [], None, None)


def test_antigravity_uses_selected_summary_database_and_macos_workspace(tmp_path, monkeypatch):
    from claude_usage_manager import provider_settings
    from claude_usage_manager.models import Period
    from claude_usage_manager.providers import MultiAgentAnalyzer

    profile = tmp_path / "selected #1"
    profile.mkdir()
    monkeypatch.setattr(provider_settings, "provider_profile", lambda _: profile)
    db = sqlite3.connect(profile / "conversation_summaries.db")
    db.execute("CREATE TABLE conversation_summaries(conversation_id, title, preview, last_modified_time,"
               "last_user_input_time, workspace_uris, source)")
    db.execute("INSERT INTO conversation_summaries VALUES(?, ?, ?, ?, ?, ?, ?)",
               ("sid", "Разбор задачи", "", "2026-08-05T11:10:00Z", None,
                '["file:///Users/test/My%20Project"]', "IDE"))
    db.commit()
    db.close()
    period = Period("p", "", dt.datetime(2026, 8, 5, 10, tzinfo=dt.timezone.utc),
                    dt.datetime(2026, 8, 5, 12, tzinfo=dt.timezone.utc))
    result = MultiAgentAnalyzer().analyze("antigravity", period, collect_quota=False)
    assert [(row.title, row.project) for row in result.threads] == [("Разбор задачи", "My Project")]
    assert _workspace_from_blobs([b"workspace /Users/test/My Project -> project/code"]) == "/Users/test/My Project"
    assert result.total_tokens == 0 and result.warnings
