"""Прогноз недели."""
import datetime as dt

import pytest

from claude_usage_manager.forecast_qt import axis_texts, series_color
from claude_usage_manager.models import QuotaWindow
from claude_usage_manager.week_forecast import OTHER_LABEL, build_chart, build_forecast

UTC = dt.timezone.utc
RESET = dt.datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
START = RESET - dt.timedelta(days=7)


def _window(used):
    return QuotaWindow("w", "Неделя", used, resets_at=RESET, window_minutes=10080, role="secondary")


def _row(hours, used):
    stamp = START + dt.timedelta(hours=hours)
    return {"ts_utc": stamp.isoformat(), "windows": [_window(used)]}


def test_projects_share_exactly_what_was_used_after_a_drop_without_reset():
    now = START + dt.timedelta(days=3)
    samples = [_row(1, 0), _row(20, 40), _row(30, 20), _row(60, 30)]
    forecast = build_forecast(_window(30), now=now, samples=samples)
    chart = build_chart(forecast, samples, {"Проект": [1.0] * 7})
    assert sum(series.total for series in chart.series) == pytest.approx(30.0)


def test_exhausted_quota_is_not_reported_as_running_out_now():
    forecast = build_forecast(_window(100), now=START + dt.timedelta(days=5))
    assert "лимит исчерпан" in axis_texts(forecast)[1]


def test_last_day_allows_the_remainder_not_hundreds_per_day():
    forecast = build_forecast(_window(40), now=RESET - dt.timedelta(hours=2))
    assert forecast.allowed_per_day == pytest.approx(60.0)


def test_day_names_follow_the_middle_of_each_day():
    forecast = build_forecast(_window(10), now=START + dt.timedelta(days=2))
    chart = build_chart(forecast, [_row(1, 1), _row(30, 10)], {})
    middle = (START + dt.timedelta(hours=12)).astimezone()
    names = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
    assert chart.day_names[0] == names[middle.weekday()]


def test_project_folder_named_other_does_not_break_the_chart():
    now = START + dt.timedelta(days=2)
    samples = [_row(1, 0), _row(40, 20)]
    forecast = build_forecast(_window(20), now=now, samples=samples)
    chart = build_chart(forecast, samples, {OTHER_LABEL: [1.0] * 7, "Демо": [1.0] * 7})
    assert sum(series.total for series in chart.series) == pytest.approx(20.0)
    assert len({series.name for series in chart.series}) == len(chart.series)


def test_series_colors_do_not_repeat_after_eight_projects():
    colors = [series_color(index, f"p{index}") for index in range(12)]
    assert len(set(colors)) == 12
