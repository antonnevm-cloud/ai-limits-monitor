import datetime as dt

import pytest

from claude_usage_manager.periods import PRESETS, build_period, parse_custom

TZ = dt.timezone(dt.timedelta(hours=3))
# Среда, 2026-08-05 14:30 (weekday()==2).
NOW = dt.datetime(2026, 8, 5, 14, 30, 0, tzinfo=TZ)


def test_all_presets_build():
    for key, _label in PRESETS:
        if key == "custom":
            continue
        period = build_period(key, NOW)
        assert period.start_local <= period.end_local


def test_today():
    p = build_period("today", NOW)
    assert p.start_local == NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    assert p.end_local == NOW


def test_yesterday():
    p = build_period("yesterday", NOW)
    assert p.start_local == dt.datetime(2026, 8, 4, 0, 0, tzinfo=TZ)
    assert p.end_local.hour == 23 and p.end_local.minute == 59


def test_last_5h_and_24h():
    assert build_period("last_5h", NOW).start_local == NOW - dt.timedelta(hours=5)
    assert build_period("last_24h", NOW).start_local == NOW - dt.timedelta(hours=24)


def test_current_week_starts_monday():
    p = build_period("current_week", NOW)
    assert p.start_local == dt.datetime(2026, 8, 3, 0, 0, tzinfo=TZ)  # понедельник
    assert p.start_local.weekday() == 0


def test_last_week():
    p = build_period("last_week", NOW)
    assert p.start_local == dt.datetime(2026, 7, 27, 0, 0, tzinfo=TZ)
    assert p.end_local.date() == dt.date(2026, 8, 2)  # воскресенье
    assert p.end_local.hour == 23


def test_current_month():
    p = build_period("current_month", NOW)
    assert p.start_local == dt.datetime(2026, 8, 1, 0, 0, tzinfo=TZ)


def test_last_month():
    p = build_period("last_month", NOW)
    assert p.start_local == dt.datetime(2026, 7, 1, 0, 0, tzinfo=TZ)
    assert p.end_local.date() == dt.date(2026, 7, 31)


def test_hourly_flag():
    assert build_period("today", NOW).hourly is True
    assert build_period("last_7d", NOW).hourly is False


def test_parse_custom_ok():
    p = parse_custom("2026-08-01 10:00", "2026-08-02 12:00", NOW)
    assert p.start_local.hour == 10
    assert p.end_local.hour == 12 and p.end_local.second == 59


def test_parse_custom_bad_format():
    with pytest.raises(ValueError):
        parse_custom("01.08.2026", "2026-08-02 12:00", NOW)


def test_parse_custom_wrong_order():
    with pytest.raises(ValueError):
        parse_custom("2026-08-02 12:00", "2026-08-01 10:00", NOW)
