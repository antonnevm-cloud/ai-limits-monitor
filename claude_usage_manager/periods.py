"""Пресеты периодов. Границы включительны: начало и конец входят в диапазон."""
from __future__ import annotations

import datetime as dt

from .models import Period

PRESETS: list[tuple[str, str]] = [
    ("today", "Сегодня"),
    ("yesterday", "Вчера"),
    ("last_5h", "Последние 5 часов"),
    ("last_24h", "Последние 24 часа"),
    ("last_7d", "Последние 7 дней"),
    ("current_week", "Текущая неделя"),
    ("last_week", "Прошлая неделя"),
    ("current_month", "Текущий месяц"),
    ("last_month", "Прошлый месяц"),
    ("custom", "Произвольный период"),
]

PRESET_LABELS = {key: label for key, label in PRESETS}


def _start_of_day(value: dt.datetime) -> dt.datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _end_of_day(value: dt.datetime) -> dt.datetime:
    return value.replace(hour=23, minute=59, second=59, microsecond=999999)


def build_period(key: str, now: dt.datetime) -> Period:
    """Строит период по ключу пресета относительно локального времени now."""
    label = PRESET_LABELS.get(key, key)
    day_start = _start_of_day(now)

    if key == "today":
        return Period(key, label, day_start, now)

    if key == "yesterday":
        yesterday = day_start - dt.timedelta(days=1)
        return Period(key, label, yesterday, _end_of_day(yesterday))

    if key == "last_5h":
        return Period(key, label, now - dt.timedelta(hours=5), now)

    if key == "last_24h":
        return Period(key, label, now - dt.timedelta(hours=24), now)

    if key == "last_7d":
        return Period(key, label, now - dt.timedelta(days=7), now)

    if key == "current_week":
        monday = day_start - dt.timedelta(days=now.weekday())
        return Period(key, label, monday, now)

    if key == "last_week":
        this_monday = day_start - dt.timedelta(days=now.weekday())
        last_monday = this_monday - dt.timedelta(days=7)
        last_sunday = _end_of_day(last_monday + dt.timedelta(days=6))
        return Period(key, label, last_monday, last_sunday)

    if key == "current_month":
        month_start = day_start.replace(day=1)
        return Period(key, label, month_start, now)

    if key == "last_month":
        first_this = day_start.replace(day=1)
        last_prev_end = _end_of_day(first_this - dt.timedelta(days=1))
        first_prev = last_prev_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return Period(key, label, first_prev, last_prev_end)

    # По умолчанию — сегодня.
    return Period("today", PRESET_LABELS["today"], day_start, now)


def parse_custom(start_text: str, end_text: str, now: dt.datetime) -> Period:
    """Разбирает произвольный период из строк 'YYYY-MM-DD HH:MM'.

    Границы включительны, конец округляется до конца минуты.
    Бросает ValueError при некорректном вводе или неверном порядке дат.
    """
    tz = now.tzinfo

    def _parse(text: str, is_end: bool) -> dt.datetime:
        text = text.strip()
        try:
            value = dt.datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError as error:
            raise ValueError(
                f"Неверный формат даты: '{text}'. Ожидается YYYY-MM-DD HH:MM."
            ) from error
        value = value.replace(tzinfo=tz)
        if is_end:
            value = value.replace(second=59, microsecond=999999)
        return value

    start = _parse(start_text, is_end=False)
    end = _parse(end_text, is_end=True)
    if end < start:
        raise ValueError("Конец периода раньше начала. Проверь порядок дат.")
    return Period("custom", PRESET_LABELS["custom"], start, end)
