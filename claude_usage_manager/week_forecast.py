"""Прогноз недельного лимита: когда он кончится при текущем темпе и как неделя росла по дням и проектам.

Неделя считается от сброса (время сброса минус 7 суток: пятница 20:00 по местному времени). Темп - взвешенный
по времени: почасовой прирост недельного процента по замерам из usage.db усредняется так, что вес часа вдвое
меньше каждые HALF_LIFE_HOURS часов (EWMA, экспоненциально взвешенное среднее). Падение процента посреди недели
(смена тарифа) сбросом не считается: отрицательный прирост равен нулю. Без замеров темп средний с начала
недели: использовано / прошедшее время. Доли проектов по дням - оценка: суточный прирост недельного процента
делится между проектами по стоимости их токенов за эти сутки.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Sequence

from .models import QuotaWindow
from .utils import parse_iso

WEEK_MINUTES = 10080
WEEK = dt.timedelta(days=7)
DAYS = 7
MIN_ELAPSED = dt.timedelta(hours=3)
HOUR = dt.timedelta(hours=1)
# Полураспад веса часа. Проверка на реальных замерах 13.11.2026 (прогноз остатка к сбросу каждые 6 часов недели):
# средняя ошибка 20,9 п.п. против 21,8 при 24 ч, 22,5 при 12 ч и 32,8 у среднего темпа с начала недели.
HALF_LIFE_HOURS = 48.0
RATE_AVERAGE = "average"  # средний темп с начала недели
RATE_WEIGHTED = "weighted"  # взвешенный по времени темп по замерам недели
OTHER_LABEL = "прочее"
MIN_SERIES_PERCENT = 3.0  # проект меньше этой доли недели на графике отдельно не показывается
MIN_OTHER_PERCENT = 1.0
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


@dataclass(frozen=True)
class WeekForecast:
    """Прогноз одной недели лимита. Время в UTC, показ переводит в местное."""

    week_start: dt.datetime
    resets_at: dt.datetime
    now: dt.datetime
    used: float
    rate_per_day: float | None
    exhausted_at: dt.datetime | None
    allowed_per_day: float | None
    method: str = RATE_AVERAGE

    @property
    def remaining(self) -> float:
        return max(0.0, 100.0 - self.used)

    @property
    def now_fraction(self) -> float:
        return _clamp((self.now - self.week_start) / WEEK)

    @property
    def exhaust_fraction(self) -> float | None:
        if self.exhausted_at is None:
            return None
        return _clamp((self.exhausted_at - self.week_start) / WEEK)

    @property
    def short_hours(self) -> int | None:
        """На сколько часов до сброса не хватит лимита; None, когда хватает или прогноза нет."""
        if self.exhausted_at is None or self.exhausted_at >= self.resets_at:
            return None
        return max(1, round((self.resets_at - self.exhausted_at).total_seconds() / 3600))

    @property
    def remaining_at_reset(self) -> float | None:
        """Сколько процентов недели останется к сбросу при темпе прогноза; None - мало данных."""
        if self.rate_per_day is None:
            return None
        left_days = max(0.0, (self.resets_at - self.now).total_seconds() / 86400)
        return max(0.0, 100.0 - self.used - self.rate_per_day * left_days)

    @property
    def enough(self) -> bool:
        return self.rate_per_day is not None and self.short_hours is None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def weekly_window(windows: Sequence[QuotaWindow]) -> QuotaWindow | None:
    """Недельное окно провайдера: 10080 минут, с процентом и временем сброса, не справочное."""
    return next(
        (
            w for w in windows
            if w.window_minutes == WEEK_MINUTES and w.used_percent is not None and w.resets_at is not None
            and not w.informational and w.role != "extra"
        ),
        None,
    )


def build_forecast(
    window: QuotaWindow,
    now: dt.datetime | None = None,
    samples: Sequence[dict[str, Any]] | None = None,
) -> WeekForecast | None:
    """Прогноз по недельному окну или None, если окно без процента или времени сброса.

    samples - строки quota_samples провайдера с начала недели: по ним темп взвешенный, без них средний.
    """
    if window.used_percent is None or window.resets_at is None:
        return None
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    resets = window.resets_at.astimezone(dt.timezone.utc)
    start = resets - WEEK
    used = max(0.0, min(100.0, float(window.used_percent)))
    elapsed = now - start
    left = resets - now
    allowed = None
    if left > dt.timedelta(0):
        # В последние сутки показывается весь остаток, а не сотни процентов "в сутки".
        allowed = max(0.0, 100.0 - used) / max(1.0, left.total_seconds() / 86400)
    rate = None
    method = RATE_AVERAGE
    exhausted = None
    if elapsed >= MIN_ELAPSED and used > 0:
        weighted = weighted_rate(_sample_points(samples, start, resets), start, now, used) if samples else None
        if weighted is not None:
            rate, method = weighted, RATE_WEIGHTED
        else:
            rate = used / (elapsed.total_seconds() / 86400)
        if rate > 0:
            exhausted = now + dt.timedelta(days=max(0.0, 100.0 - used) / rate)
    return WeekForecast(start, resets, now, used, rate, exhausted, allowed, method)


def weighted_rate(points: list[tuple[dt.datetime, float]], start: dt.datetime, now: dt.datetime, used: float) -> float | None:
    """Темп в процентах за сутки: взвешенное среднее почасового прироста, вес часа вдвое меньше каждые HALF_LIFE_HOURS.

    Часы отсчитываются назад от «сейчас» до начала недели; значение на границе часа - линейно между замерами
    (до первого замера - от нуля в начале недели), на «сейчас» - текущий процент окна. Отрицательный прирост
    (процент упал без сброса, например при смене тарифа) равен нулю. None - замеров недели нет или прошло меньше часа.
    """
    hours = int((now - start) // HOUR)
    if hours < 1 or not points:
        return None
    edges = [now - HOUR * k for k in range(hours, -1, -1)]
    values = _values_at(points, edges, start)
    values[-1] = used
    increments = [max(0.0, right - left) for left, right in zip(values, values[1:])]
    count = len(increments)
    weights = [0.5 ** ((count - 1 - index) / HALF_LIFE_HOURS) for index in range(count)]
    return 24.0 * sum(weight * value for weight, value in zip(weights, increments)) / sum(weights)


def weekday_name(moment: dt.datetime) -> str:
    return WEEKDAYS[moment.astimezone().weekday()]


def clock_text(moment: dt.datetime) -> str:
    """«вс 02:00» в местном времени."""
    local = moment.astimezone()
    return f"{WEEKDAYS[local.weekday()]} {local:%H:%M}"


# --------------------------------------------------------------------------- #
# График недели
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChartSeries:
    """Один проект: накопленный процент недели на границах суток 0..DAYS."""

    name: str
    cumulative: tuple[float, ...]

    @property
    def total(self) -> float:
        return self.cumulative[-1]

    def growth(self, day: int) -> float:
        return self.cumulative[day + 1] - self.cumulative[day]


@dataclass(frozen=True)
class WeekChart:
    forecast: WeekForecast
    series: tuple[ChartSeries, ...]
    boundaries: tuple[float, ...]  # доли недели, на которых заданы накопленные значения (последняя - «сейчас»)
    day_names: tuple[str, ...]


def _sample_points(samples: Sequence[dict[str, Any]], start: dt.datetime, resets: dt.datetime) -> list[tuple[dt.datetime, float]]:
    """Замеры недельного процента этой недели по возрастанию времени."""
    points: list[tuple[dt.datetime, float]] = []
    for row in samples:
        stamp = parse_iso(row.get("ts_utc"))
        if stamp is None:
            continue
        window = weekly_window(row.get("windows") or [])
        if window is None or abs((window.resets_at.astimezone(dt.timezone.utc) - resets).total_seconds()) > 600:
            continue
        points.append((stamp.astimezone(dt.timezone.utc), float(window.used_percent)))
    points.sort(key=lambda item: item[0])
    return points


def _values_at(points: list[tuple[dt.datetime, float]], moments: Sequence[dt.datetime], start: dt.datetime) -> list[float]:
    """Процент недели на моменты (по возрастанию): линейно между замерами, до первого замера - от нуля в начале недели."""
    result: list[float] = []
    index = 0
    before_time, before_value = start, 0.0
    for moment in moments:
        if not points or moment <= start:
            result.append(0.0)
            continue
        while index < len(points) and points[index][0] < moment:
            before_time, before_value = points[index]
            index += 1
        if index == len(points):
            result.append(before_value)
            continue
        stamp, value = points[index]
        span = (stamp - before_time).total_seconds()
        result.append(value if span <= 0 else before_value + (value - before_value) * ((moment - before_time).total_seconds() / span))
    return result


def build_chart(
    forecast: WeekForecast,
    samples: Sequence[dict[str, Any]],
    daily_cost: dict[str, list[float]],
) -> WeekChart | None:
    """Накопленный расход недели по проектам на границах суток; None, когда замеров недели нет."""
    points = _sample_points(samples, forecast.week_start, forecast.resets_at)
    if not points:
        return None
    start = forecast.week_start
    full_days = min(DAYS, int((forecast.now - start) // dt.timedelta(days=1)))
    edges = [start + dt.timedelta(days=k) for k in range(full_days + 1)]
    if forecast.now > edges[-1]:
        edges.append(forecast.now)
    values = _values_at(points, edges, start)
    # Потеря точности последнего замера: к «сейчас» значение равно текущему проценту окна.
    values[-1] = forecast.used
    names = sorted(daily_cost, key=lambda name: -sum(daily_cost[name]))
    # Внутренний ключ "прочего" не совпадает с папкой проекта, названной "прочее".
    other_key = "\x00other"
    cumulative = {name: [0.0] for name in names}
    cumulative[other_key] = [0.0]
    for index in range(len(edges) - 1):
        growth = max(0.0, values[index + 1] - values[index])
        day = min(DAYS - 1, index)  # отрезок index лежит в сутках index; неполный последний отрезок - в текущих сутках
        costs = {name: daily_cost[name][day] for name in names}
        total = sum(costs.values())
        for name in names:
            cumulative[name].append(cumulative[name][-1] + (growth * costs[name] / total if total > 0 else 0.0))
        cumulative[other_key].append(cumulative[other_key][-1] + (growth if total <= 0 else 0.0))
    # Если процент падал без сброса (смена тарифа), сумма приростов больше израсходованного:
    # проекты делят ровно израсходованное к "сейчас".
    grown = sum(values[-1] for values in cumulative.values())
    if grown > forecast.used > 0:
        factor = forecast.used / grown
        cumulative = {name: [value * factor for value in values] for name, values in cumulative.items()}
    big = [name for name in names if cumulative[name][-1] >= MIN_SERIES_PERCENT]
    other = list(cumulative[other_key])
    for name in names:
        if name not in big:
            other = [left + right for left, right in zip(other, cumulative[name])]
    series = [ChartSeries(name if name != OTHER_LABEL else f"{name} (папка)", tuple(cumulative[name])) for name in big]
    if other[-1] >= MIN_OTHER_PERCENT:
        series.append(ChartSeries(OTHER_LABEL, tuple(other)))
    boundaries = tuple(_clamp((edge - start) / WEEK) for edge in edges)
    # Сутки графика названы по дню, на который приходится их середина, а не начало отрезка.
    day_names = tuple(WEEKDAYS[(start + dt.timedelta(days=k, hours=12)).astimezone().weekday()] for k in range(DAYS))
    return WeekChart(forecast, tuple(series), boundaries, day_names)
