"""Календарь ежемесячных платежей по подпискам."""

import calendar
import datetime as dt


def parse_last_payment(text: str, today: dt.date) -> dt.date:
    """Восстановить последнюю прошедшую дату по числу и месяцу, включая сегодня."""
    day_text, month_text = text.split('.')
    day, month = int(day_text), int(month_text)
    year = today.year
    if (month, day) > (today.month, today.day):
        year -= 1
    if (month, day) == (2, 29):
        while not calendar.isleap(year):
            year -= 1
    return dt.date(year, month, day)


def next_monthly_payment(billing_day: int, today: dt.date) -> dt.date:
    """Ближайшее число оплаты строго после сегодняшней даты."""
    last_day = calendar.monthrange(today.year, today.month)[1]
    due = today.replace(day=min(billing_day, last_day))
    if due > today:
        return due
    next_month = today.replace(day=last_day) + dt.timedelta(days=1)
    last_day = calendar.monthrange(next_month.year, next_month.month)[1]
    return next_month.replace(day=min(billing_day, last_day))
