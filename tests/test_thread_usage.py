import datetime as dt
import json

import pytest

from claude_usage_manager import pricing
from claude_usage_manager.thread_usage import UsageScanner


# ------------------------------- Цены -------------------------------------- #
def test_claude_cost_uses_official_rates_per_cache_tier():
    usage = {"input_tokens": 1000, "cache_creation": {"ephemeral_5m_input_tokens": 2000, "ephemeral_1h_input_tokens": 0},
             "cache_read_input_tokens": 10000, "output_tokens": 500}
    # Opus 5.5: вход $4, запись 5 минут $5, чтение $0,20, выход $20 за миллион
    assert pricing.claude_cost("claude-opus-5-5", usage) == pytest.approx((1000 * 4 + 2000 * 5 + 10000 * 0.2 + 500 * 20) / 1e6)
    assert pricing.claude_cost("claude-opus-4-5-20251101", usage) == pytest.approx((1000 * 5 + 2000 * 6.25 + 10000 * 0.5 + 500 * 25) / 1e6)
    assert pricing.claude_cost("claude-unknown-9", usage) is None
    assert pricing.claude_cost("<synthetic>", usage) is None


def test_claude_without_tier_split_counts_write_as_five_minutes():
    usage = {"input_tokens": 0, "cache_creation_input_tokens": 1000, "output_tokens": 0}
    assert pricing.claude_cost("claude-sonnet-5-5", usage) == pytest.approx(1000 * 2.5 / 1e6)


def test_haiku_long_prompt_tier():
    short = {"input_tokens": 50_000, "output_tokens": 1000}
    long = {"input_tokens": 50_000, "cache_read_input_tokens": 60_000, "output_tokens": 1000}
    assert pricing.claude_cost("claude-haiku-5-5", short) == pytest.approx((50_000 * 0.10 + 1000 * 0.50) / 1e6)
    assert pricing.claude_cost("claude-haiku-5-5", long) == pytest.approx((50_000 * 0.50 + 60_000 * 0.05 + 1000 * 2.50) / 1e6)


def test_claude_rewrite_cost_uses_the_tier_the_cache_was_written_to():
    hour = {"input_tokens": 10, "cache_creation": {"ephemeral_1h_input_tokens": 5000, "ephemeral_5m_input_tokens": 0},
            "cache_read_input_tokens": 95_000, "output_tokens": 300}
    assert pricing.claude_rewrite_cost("claude-opus-5-5", hour) == pytest.approx(100_010 * 8 / 1e6)
    five = dict(hour, cache_creation={"ephemeral_5m_input_tokens": 5000})
    assert pricing.claude_rewrite_cost("claude-opus-5-5", five) == pytest.approx(100_010 * 5 / 1e6)
    assert pricing.claude_rewrite_cost("claude-x", hour) is None


def test_codex_cost_splits_cached_written_and_fresh_input():
    usage = {"input_tokens": 10_000, "cached_input_tokens": 8000, "cache_write_input_tokens": 1000, "output_tokens": 500}
    # gpt-6.1-sol: вход $2, кэш $0,10, запись $2,50, выход $10
    assert pricing.codex_cost("gpt-6.1-sol", usage) == pytest.approx((1000 * 2 + 8000 * 0.10 + 1000 * 2.5 + 500 * 10) / 1e6)
    assert pricing.codex_cost("gpt-5.1-codex", usage) is None


def test_codex_long_context_and_rewrite_without_write_price():
    usage = {"input_tokens": 300_000, "cached_input_tokens": 0, "output_tokens": 0}
    assert pricing.codex_cost("gpt-5.6-sol", usage) == pytest.approx(300_000 * 8 / 1e6)
    assert pricing.codex_rewrite_cost("gpt-5.4", {"input_tokens": 100_000}) == pytest.approx(100_000 * 2.5 / 1e6)
    assert pricing.codex_rewrite_cost("gpt-6.1-sol", {"input_tokens": 100_000}) == pytest.approx(100_000 * 2.5 / 1e6)


@pytest.mark.parametrize("value,text", [(3.4, "$3,40"), (14.2, "$14,20"), (0.05, "$0,05"), (0.001, "<$0,01"), (0, "$0,00")])
def test_money_format(value, text):
    assert pricing.format_money(value) == text


# --------------------------- Расход по журналам ------------------------------ #
def utc(*args):
    return dt.datetime(*args, tzinfo=dt.timezone.utc)


def claude_line(message_id, stamp, usage, model="claude-opus-5-5", sidechain=False, cwd="C:/Workspace/Clients/Example/trade_demo"):
    return json.dumps({
        "type": "assistant", "timestamp": stamp.isoformat().replace("+00:00", "Z"), "cwd": cwd, "isSidechain": sidechain,
        "message": {"id": message_id, "model": model, "usage": usage},
    }, separators=(",", ":")) + "\n"


def codex_lines(session, model, records):
    lines = [
        {"type": "session_meta", "timestamp": "2026-11-12T08:00:00Z", "payload": {"session_id": session, "id": session, "cwd": "C:/Workspace/demo-server"}},
        {"type": "turn_context", "timestamp": "2026-11-12T08:00:01Z", "payload": {"model": model}},
    ]
    for response_id, stamp, usage in records:
        lines.append({"type": "token_usage_record", "timestamp": stamp.isoformat().replace("+00:00", "Z"),
                      "payload": {"session_id": session, "thread_id": session, "response_id": response_id, "usage": usage}})
    return "".join(json.dumps(item) + "\n" for item in lines)


USAGE = {"input_tokens": 1000, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
         "cache_read_input_tokens": 0, "output_tokens": 0}


def cost_of(tokens, rate):
    return tokens * rate / 1e6


@pytest.fixture
def journals(tmp_path):
    claude = tmp_path / "claude"
    project = claude / "C--Workspace-Clients-Example-trade-demo"
    project.mkdir(parents=True)
    week_start = utc(2026, 11, 9, 9, 0)
    main = project / "s1.jsonl"
    main.write_text(
        claude_line("old", week_start - dt.timedelta(days=2), USAGE)  # до начала недели: в расход не идет
        + claude_line("m1", week_start + dt.timedelta(hours=1), dict(USAGE, output_tokens=10))
        + claude_line("m1", week_start + dt.timedelta(hours=1, seconds=2), dict(USAGE, output_tokens=100))  # тот же ответ: берется последний кусок
        + claude_line("synthetic-1", week_start + dt.timedelta(hours=2), USAGE, model="<synthetic>")
        + claude_line("m2", week_start + dt.timedelta(days=1, hours=1), dict(USAGE, cache_read_input_tokens=50_000))
        + claude_line("side", week_start + dt.timedelta(days=1, hours=3), USAGE, sidechain=True),
        encoding="utf-8",
    )
    sub = project / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a.jsonl").write_text(claude_line("sub1", week_start + dt.timedelta(days=1, hours=4), USAGE, model="claude-sonnet-5-5"), encoding="utf-8")
    other = claude / "C--Workspace-other"
    other.mkdir()
    (other / "s2.jsonl").write_text(claude_line("x1", week_start + dt.timedelta(hours=5), dict(USAGE, input_tokens=3000), cwd="C:/Workspace/sample-app"), encoding="utf-8")
    codex = tmp_path / "codex"
    now = dt.datetime.now(dt.timezone.utc)
    day = codex / f"{now.astimezone():%Y}" / f"{now.astimezone():%m}" / f"{now.astimezone():%d}"
    day.mkdir(parents=True)
    rollout = day / "rollout-a.jsonl"
    rollout.write_text(codex_lines("c1", "gpt-6.1-sol", [
        ("r1", now - dt.timedelta(hours=2), {"input_tokens": 10_000, "cached_input_tokens": 0, "output_tokens": 0}),
        ("r2", now - dt.timedelta(hours=1), {"input_tokens": 20_000, "cached_input_tokens": 10_000, "output_tokens": 0}),
    ]), encoding="utf-8")
    return claude, codex, week_start, main, rollout, now


def test_claude_spend_counts_each_answer_once_and_includes_subagents(journals):
    claude, codex, week_start, main, _rollout, _now = journals
    scanner = UsageScanner(claude, None)
    report = scanner.scan({"claude": week_start}, {"claude": 30.0}, now=week_start + dt.timedelta(days=3))
    s1 = report.spend("claude", "s1")
    opus_1000 = cost_of(1000, 4)
    expected = (opus_1000 + cost_of(100, 20)) + (opus_1000 + cost_of(50_000, 0.20)) + opus_1000 + cost_of(1000, 2)
    assert s1.cost == pytest.approx(expected)
    assert s1.tokens == 1100 + 51_000 + 1000 + 1000 and s1.unpriced_tokens == 0
    # перезапись считается по последнему ответу основного треда (не субагента): 51 000 токенов по цене записи 5 минут
    assert s1.rewrite == pytest.approx(cost_of(51_000, 5))
    s2 = report.spend("claude", "s2")
    assert s2.cost == pytest.approx(cost_of(3000, 4))
    # доля недели делится по стоимости и в сумме дает недельный процент
    assert s1.share + s2.share == pytest.approx(30.0)
    assert s1.share / s2.share == pytest.approx(s1.cost / s2.cost)
    daily = report.daily["claude"]
    assert set(daily) == {"trade_demo", "sample-app"}
    assert daily["trade_demo"][0] == pytest.approx(opus_1000 + cost_of(100, 20))
    assert daily["trade_demo"][1] == pytest.approx(opus_1000 + cost_of(50_000, 0.20) + opus_1000 + cost_of(1000, 2))


def test_rescan_reads_only_new_complete_lines(journals):
    claude, _codex, week_start, main, _rollout, _now = journals
    scanner = UsageScanner(claude, None)
    now = week_start + dt.timedelta(days=3)
    first = scanner.scan({"claude": week_start}, {"claude": 30.0}, now=now).spend("claude", "s1").cost
    line = claude_line("m3", week_start + dt.timedelta(days=2), USAGE)
    with main.open("a", encoding="utf-8") as stream:
        stream.write(line[:-20])  # строка еще пишется
    assert scanner.scan({"claude": week_start}, {"claude": 30.0}, now=now).spend("claude", "s1").cost == pytest.approx(first)
    with main.open("a", encoding="utf-8") as stream:
        stream.write(line[-20:])
    assert scanner.scan({"claude": week_start}, {"claude": 30.0}, now=now).spend("claude", "s1").cost == pytest.approx(first + cost_of(1000, 4))


def test_codex_spend_uses_turn_model_and_response_ids(journals):
    _claude, codex, _week_start, _main, rollout, now = journals
    scanner = UsageScanner(None, codex)
    report = scanner.scan({"codex": now - dt.timedelta(days=1)}, {"codex": 12.0}, now=now)
    c1 = report.spend("codex", "c1")
    assert c1.cost == pytest.approx(cost_of(10_000, 2) + cost_of(10_000, 2) + cost_of(10_000, 0.10))
    assert c1.share == pytest.approx(12.0)  # единственный тред недели забирает весь процент
    assert c1.rewrite == pytest.approx(cost_of(20_000, 2.5))
    assert report.daily["codex"]["demo-server"][0] == pytest.approx(c1.cost)


def test_unknown_model_is_counted_as_unpriced_not_guessed(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    start = utc(2026, 11, 9)
    (project / "s.jsonl").write_text(claude_line("a", start + dt.timedelta(hours=1), USAGE, model="claude-new-9"), encoding="utf-8")
    report = UsageScanner(tmp_path, None).scan({"claude": start}, {"claude": 10.0}, now=start + dt.timedelta(days=1))
    spend = report.spend("claude", "s")
    assert spend.cost == 0 and spend.unpriced_tokens == 1000 and spend.share is None and spend.rewrite is None
    assert not spend.priced


def test_missing_directories_give_an_empty_report(tmp_path):
    report = UsageScanner(tmp_path / "нет", tmp_path / "тоже нет").scan({}, {})
    assert report.threads == {} and report.daily == {}


def test_weekly_scanner_changes_profile_without_reusing_former_spend(tmp_path, monkeypatch):
    from claude_usage_manager import provider_settings

    roots = [tmp_path / "profile-a", tmp_path / "profile-b"]
    stamp = dt.datetime.now(dt.timezone.utc)
    for root, tokens in zip(roots, [10, 100]):
        path = root / "archived_sessions" / "rollout-same-id.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(codex_lines("shared", "gpt-6.1-sol", [
            ("r1", stamp, {"input_tokens": tokens}),
        ]), encoding="utf-8")
    monkeypatch.setattr(provider_settings, "provider_enabled", lambda key: key == "codex")
    profile = roots[0]
    monkeypatch.setattr(provider_settings, "provider_profile", lambda _: profile)
    scanner = UsageScanner()
    assert scanner.scan({}, {}, now=stamp).spend("codex", "shared").tokens == 10
    profile = roots[1]
    assert scanner.scan({}, {}, now=stamp).spend("codex", "shared").tokens == 100
    monkeypatch.setattr(provider_settings, "provider_enabled", lambda _: False)
    assert scanner.scan({}, {}, now=stamp).threads == {}


def test_large_assistant_record_is_processed_only_after_its_newline(tmp_path, monkeypatch):
    from claude_usage_manager import history_jsonl

    monkeypatch.setattr(history_jsonl, "CHUNK_BYTES", 64)
    project = tmp_path / "project"
    project.mkdir()
    path = project / "session.jsonl"
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    row = json.loads(claude_line("large", start, USAGE))
    row["message"] = {
        "content": [{"text": '"quote" \\ text' * 50}, {"items": [1, {"text": "text" * 100}]}],
        **row["message"],
    }
    serialized = json.dumps(row, ensure_ascii=False)
    path.write_text(serialized, encoding="utf-8")
    scanner = UsageScanner(tmp_path, None)
    assert scanner.scan({"claude": start}, {}).spend("claude", "session") is None

    with path.open("a", encoding="utf-8") as stream:
        stream.write("\n")
        stream.write('{"bad": [' + "9" * 300 + '}\n')
        stream.write(claude_line("normal", start, USAGE))
    spend = scanner.scan({"claude": start}, {}).spend("claude", "session")
    assert spend.tokens == 2000


def test_replaced_claude_file_removes_retained_usage_and_keeps_subagent_usage(journals):
    claude, _, start, main, _, _ = journals
    now = start + dt.timedelta(days=3)
    scanner = UsageScanner(claude, None)
    first = scanner.scan({"claude": start}, {}, now).spend("claude", "s1")
    replacement = main.with_suffix(".tmp")
    replacement.write_text(claude_line("new", now, USAGE), encoding="utf-8")
    replacement.replace(main)

    updated = scanner.scan({"claude": start}, {}, now).spend("claude", "s1")
    assert first.tokens > updated.tokens == 2000  # один основной ответ и один ответ субагента
    main.unlink()
    assert scanner.scan({"claude": start}, {}, now).spend("claude", "s1").tokens == 1000


def test_codex_period_prunes_previous_usage_and_rewinds_to_earlier_start(tmp_path):
    import os

    profile = tmp_path / "profile"
    archived = profile / "archived_sessions"
    archived.mkdir(parents=True)
    path = archived / "rollout.jsonl"
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)
    recent = start + dt.timedelta(hours=1)
    path.write_text(codex_lines("session", "gpt-6.1-sol", [
        ("earlier", recent - dt.timedelta(days=4), {"input_tokens": 100}),
        ("recent", recent, {"input_tokens": 1000}),
    ]), encoding="utf-8")
    os.utime(path, (recent.timestamp(), recent.timestamp()))
    scanner = UsageScanner(None, profile / "sessions")

    assert scanner.scan({"codex": start}, {}).spend("codex", "session").tokens == 1000
    assert scanner.scan({"codex": start - dt.timedelta(days=5)}, {}).spend("codex", "session").tokens == 1100
    assert scanner.scan({"codex": start + dt.timedelta(days=2)}, {}).threads == {}
    assert not scanner._history["codex"].files
    assert not scanner._history["codex"].sessions
    assert scanner.scan({"codex": start}, {}).spend("codex", "session").tokens == 1000
