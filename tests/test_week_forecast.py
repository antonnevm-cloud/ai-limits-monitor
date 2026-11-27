import datetime as dt

import pytest

from claude_usage_manager.database import Database
from claude_usage_manager.forecast_qt import axis_texts, chart_info, rate_text, rate_value, reset_html, reset_remaining_text, status_text
from claude_usage_manager.models import QuotaSnapshot, QuotaWindow
from claude_usage_manager.week_forecast import OTHER_LABEL, build_chart, build_forecast, clock_text, weekly_window

NOW = dt.datetime(2026, 11, 13, 9, 0, tzinfo=dt.timezone.utc)


def week(used, resets=NOW + dt.timedelta(days=3), minutes=10080, **extra):
    return QuotaWindow("secondary", "week", used, resets_at=resets, window_minutes=minutes, role="secondary", **extra)


def test_forecast_matches_the_mockup_numbers():
    forecast = build_forecast(week(70), now=NOW)
    assert forecast.week_start == NOW - dt.timedelta(days=4)
    assert forecast.rate_per_day == pytest.approx(17.5)
    assert forecast.allowed_per_day == pytest.approx(10.0)
    assert forecast.exhausted_at == NOW + dt.timedelta(days=30 / 17.5)
    assert forecast.short_hours == 31
    assert forecast.now_fraction == pytest.approx(4 / 7)
    assert forecast.exhaust_fraction == pytest.approx((4 + 30 / 17.5) / 7)
    assert status_text(forecast) == "не хватит на 31 ч" and forecast.remaining_at_reset == 0.0
    now, right = axis_texts(forecast)  # дня начала недели слева больше нет: он совпадал с днем сброса
    assert now == "сейчас · осталось 30%"
    assert right == f"кончится {clock_text(forecast.exhausted_at)} · сброс {clock_text(forecast.resets_at)}"
    assert reset_remaining_text(forecast) is None
    assert f"кончится {clock_text(forecast.exhausted_at)}</span> · сброс" in reset_html(forecast)


def test_enough_limit_and_too_early_week():
    enough = build_forecast(week(20), now=NOW)
    assert enough.short_hours is None and enough.enough
    # 20% за 4 дня - 5% в сутки; за оставшиеся 3 дня уйдет еще 15%, к сбросу останется 65%
    assert enough.remaining_at_reset == pytest.approx(65.0)
    assert status_text(enough) == "к сбросу останется ~65%"
    assert axis_texts(enough)[1] == f"сброс {clock_text(enough.resets_at)}"
    # остаток к сбросу - через пробел после «сброс …», цветом
    assert reset_remaining_text(enough) == "~65%"
    assert reset_html(enough).startswith(f"сброс {clock_text(enough.resets_at)} <span") and reset_html(enough).endswith(">~65%</span>")
    early = build_forecast(week(5, resets=NOW + dt.timedelta(days=7) - dt.timedelta(hours=1)), now=NOW)
    assert early.rate_per_day is None and early.exhausted_at is None
    assert status_text(early) == "мало данных для прогноза" and early.remaining_at_reset is None
    assert reset_html(early) == f"сброс {clock_text(early.resets_at)}"
    assert build_forecast(week(None), now=NOW) is None


def test_weekly_window_picks_only_a_real_week():
    windows = [
        QuotaWindow("primary", "5h", 10, resets_at=NOW, window_minutes=300, role="primary"),
        week(40, informational=True),
        week(50),
    ]
    assert weekly_window(windows).used_percent == 50
    assert weekly_window([week(50, resets=None)]) is None


def test_chart_splits_daily_growth_between_projects_by_cost(tmp_path):
    db = Database(tmp_path / "f.db")
    forecast = build_forecast(week(70), now=NOW)
    start = forecast.week_start
    for day, used in enumerate([0, 15, 33, 52]):
        snapshot = QuotaSnapshot(provider="claude", source="claude-code:official-mod", windows=[week(used, resets=forecast.resets_at)])
        db._conn.execute(
            "INSERT INTO quota_samples (ts_utc,weekly,source,windows_json) VALUES (?,?,?,?)",
            ((start + dt.timedelta(days=day)).isoformat(), used, "claude-code:official-mod", db._windows_json(snapshot)),
        )
    # замер прошлой недели не должен попасть в график
    old = QuotaSnapshot(provider="claude", source="claude-code:official-mod", windows=[week(99, resets=start)])
    db._conn.execute("INSERT INTO quota_samples (ts_utc,weekly,source,windows_json) VALUES (?,?,?,?)",
                     ((start + dt.timedelta(hours=1)).isoformat(), 99, "claude-code:official-mod", db._windows_json(old)))
    db._conn.commit()
    samples = db.quota_samples(start_utc=start, provider="claude")
    daily = {"a": [1, 1, 0, 3, 0, 0, 0], "b": [1, 0, 0, 1, 0, 0, 0]}
    chart = build_chart(forecast, samples, daily)
    db.close()
    assert chart.boundaries == pytest.approx((0, 1 / 7, 2 / 7, 3 / 7, 4 / 7))
    totals = {item.name: item.total for item in chart.series}
    # пн +15 пополам, вт +18 только a, ср +19 без расхода в журналах -> «прочее», чт +18 делится 3:1
    assert totals["a"] == pytest.approx(7.5 + 18 + 13.5)
    assert totals["b"] == pytest.approx(7.5 + 4.5)
    assert totals[OTHER_LABEL] == pytest.approx(19)
    assert sum(totals.values()) == pytest.approx(70)
    info = chart_info(chart)
    assert info.startswith("Резче всего неделя росла") and "Больше всего за неделю: a, 39%" in info
    detail = chart_info(chart, [item.name for item in chart.series].index("b"))
    assert detail.startswith("b: 12% недели (17% всего расхода)")


def test_chart_needs_samples_of_this_week():
    forecast = build_forecast(week(70), now=NOW)
    assert build_chart(forecast, [], {"a": [1] * 7}) is None


def test_small_projects_go_to_other_instead_of_zero_percent_rows(tmp_path):
    db = Database(tmp_path / "small.db")
    forecast = build_forecast(week(40), now=NOW)
    snapshot = QuotaSnapshot(provider="claude", source="claude-code:official-mod", windows=[week(20, resets=forecast.resets_at)])
    db._conn.execute("INSERT INTO quota_samples (ts_utc,weekly,source,windows_json) VALUES (?,?,?,?)",
                     ((forecast.week_start + dt.timedelta(days=2)).isoformat(), 20, "claude-code:official-mod", db._windows_json(snapshot)))
    db._conn.commit()
    samples = db.quota_samples(start_utc=forecast.week_start, provider="claude")
    db.close()
    daily = {"big": [10, 10, 10, 10, 0, 0, 0], "tiny1": [0.1, 0, 0, 0, 0, 0, 0], "tiny2": [0, 0, 0, 0.2, 0, 0, 0], "zero": [0, 0, 0, 0, 0, 0, 0]}
    chart = build_chart(forecast, samples, daily)
    names = [item.name for item in chart.series]
    assert names[0] == "big" and "tiny1" not in names and "tiny2" not in names and "zero" not in names
    assert all(item.total >= 1.0 for item in chart.series)
    assert sum(item.total for item in chart.series) == pytest.approx(40, abs=1.0)


def weekly_samples(forecast_resets, points, source="claude-code:official-mod"):
    """Строки quota_samples в том виде, в каком их отдает база: время и окна."""
    return [
        {"ts_utc": stamp.isoformat(), "source": source, "windows": [week(used, resets=forecast_resets)]}
        for stamp, used in points
    ]


def test_weighted_rate_follows_recent_hours_from_the_friday_reset():
    resets = NOW + dt.timedelta(days=3)
    start = resets - dt.timedelta(days=7)
    # три дня по чуть-чуть (10%), последние сутки резко (+30%): средний темп 10% в сутки, за последние сутки 30%
    samples = weekly_samples(resets, [(start + dt.timedelta(days=3), 10), (NOW, 40)])
    forecast = build_forecast(week(40, resets=resets), now=NOW, samples=samples)
    assert forecast.week_start == start and forecast.method == "weighted"
    # вес часа вдвое меньше каждые 48 ч: 24 часа по 1,25% и 72 часа по 10/72%
    weights = [0.5 ** (age / 48) for age in range(96)]
    hourly = [1.25 if age < 24 else 10 / 72 for age in range(96)]
    expected = 24 * sum(w * x for w, x in zip(weights, hourly)) / sum(weights)
    assert forecast.rate_per_day == pytest.approx(expected)
    assert 10 < forecast.rate_per_day < 30
    assert forecast.remaining_at_reset == pytest.approx(max(0.0, 60 - expected * 3))
    average = build_forecast(week(40, resets=resets), now=NOW)
    assert average.method == "average" and average.rate_per_day == pytest.approx(10)
    assert forecast.remaining_at_reset < average.remaining_at_reset


def test_tariff_drop_mid_week_is_not_a_reset():
    resets = NOW + dt.timedelta(days=3)
    start = resets - dt.timedelta(days=7)
    # 01.11: процент упал с 25 до 0 при смене тарифа; неделя все равно считается от пятничного сброса
    samples = weekly_samples(resets, [(start + dt.timedelta(days=1), 25), (start + dt.timedelta(days=1, hours=1), 0), (NOW, 5)])
    forecast = build_forecast(week(5, resets=resets), now=NOW, samples=samples)
    assert forecast.week_start == start and forecast.method == "weighted"
    # падение не уменьшает темп: прирост первого дня учтен, отрицательный прирост равен нулю
    later = weekly_samples(resets, [(start + dt.timedelta(days=1, hours=1), 0), (NOW, 5)])
    assert forecast.rate_per_day > build_forecast(week(5, resets=resets), now=NOW, samples=later).rate_per_day > 0
    assert forecast.remaining_at_reset == pytest.approx(max(0.0, 95 - forecast.rate_per_day * 3))


def test_samples_of_another_week_fall_back_to_the_average_rate():
    resets = NOW + dt.timedelta(days=3)
    old = weekly_samples(resets - dt.timedelta(days=7), [(NOW - dt.timedelta(hours=5), 90)])
    forecast = build_forecast(week(20, resets=resets), now=NOW, samples=old)
    assert forecast.method == "average" and forecast.rate_per_day == pytest.approx(5)


def test_rate_texts_name_the_method():
    resets = NOW + dt.timedelta(days=3)
    weighted = build_forecast(week(40, resets=resets), now=NOW, samples=weekly_samples(resets, [(NOW, 40)]))
    assert rate_value(weighted).startswith("~") and rate_value(weighted).endswith("% в сутки")
    assert "свежие часы весят больше" in rate_text(weighted) and clock_text(weighted.week_start) in rate_text(weighted)
    average = build_forecast(week(20, resets=resets), now=NOW)
    assert rate_value(average) == "5,0% в сутки" and rate_text(average).startswith("Средний темп 5,0% в сутки")
    early = build_forecast(week(5, resets=NOW + dt.timedelta(days=7) - dt.timedelta(hours=1)), now=NOW)
    assert rate_value(early) == "мало данных" and rate_text(early).startswith("Темпа пока нет")
