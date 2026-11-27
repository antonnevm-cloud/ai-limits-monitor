import datetime as dt

import pytest

from claude_usage_manager.subscription_payments import next_monthly_payment, parse_last_payment


@pytest.mark.parametrize(('text', 'today', 'last_paid', 'due'), [
    ('04.11', '2026-11-25', '2026-11-04', '2026-12-04'),
    ('28.10', '2026-11-25', '2026-10-28', '2026-11-28'),
    ('31.12', '2026-01-01', '2025-12-31', '2026-01-31'),
    ('31.01', '2026-02-27', '2026-01-31', '2026-02-28'),
    ('31.01', '2026-02-28', '2026-01-31', '2026-03-31'),
    ('31.01', '2028-02-28', '2028-01-31', '2028-02-29'),
    ('29.02', '2026-12-29', '2024-02-29', '2027-01-29'),
])
def test_monthly_payment_calendar(text, today, last_paid, due):
    current = dt.date.fromisoformat(today)
    anchor = parse_last_payment(text, current)
    assert anchor == dt.date.fromisoformat(last_paid)
    assert next_monthly_payment(anchor.day, current) == dt.date.fromisoformat(due)
