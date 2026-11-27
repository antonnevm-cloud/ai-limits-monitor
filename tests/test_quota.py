import datetime as dt
import json

import pytest

from claude_usage_manager import quota
from claude_usage_manager.models import ManualResetCredits, Period

UTC = dt.timezone.utc


def _ms(y, mo, d, h, mi):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def _period():
    start = dt.datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    end = dt.datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    return Period("custom", "Тест", start.astimezone(), end.astimezone())


def test_weekly_consumed_with_unexplained_drop_is_unknown(monkeypatch):
    samples = [
        (_ms(2026, 8, 5, 9, 0), 30.0),    # базовый до периода
        (_ms(2026, 8, 5, 10, 30), 40.0),  # +10
        (_ms(2026, 8, 5, 11, 0), 20.0),   # неизвестное снижение
        (_ms(2026, 8, 5, 11, 30), 25.0),
    ]
    monkeypatch.setattr(quota, "_history_samples", lambda: samples)
    consumed = quota.weekly_consumed(_period(), None, current_weekly=30.0)
    assert consumed is None


def test_weekly_consumed_ignores_small_jitter(monkeypatch):
    samples = [
        (_ms(2026, 8, 5, 9, 0), 30.0),
        (_ms(2026, 8, 5, 10, 30), 39.0),
        (_ms(2026, 8, 5, 10, 45), 38.0),
        (_ms(2026, 8, 5, 11, 0), 39.0),
    ]
    monkeypatch.setattr(quota, "_history_samples", lambda: samples)

    consumed = quota.weekly_consumed(_period(), None, current_weekly=38.0)

    assert abs(consumed - 9.0) < 1e-6


def test_weekly_consumed_counts_confirmed_reset(monkeypatch):
    samples = [
        (_ms(2026, 8, 5, 9, 0), 30.0),
        (_ms(2026, 8, 5, 10, 30), 51.0),
        (_ms(2026, 8, 5, 11, 0), 0.0),
        (_ms(2026, 8, 5, 11, 30), 4.0),
    ]
    monkeypatch.setattr(quota, "_history_samples", lambda: samples)

    consumed = quota.weekly_consumed(_period(), None, current_weekly=5.0)

    assert abs(consumed - 26.0) < 1e-6


def test_weekly_consumed_no_data(monkeypatch):
    monkeypatch.setattr(quota, "_history_samples", lambda: [])
    assert quota.weekly_consumed(_period(), None, current_weekly=None) is None


def test_weekly_consumed_prefers_native_history_over_conflicting_database(monkeypatch):
    history = [
        (_ms(2026, 8, 5, 9, 0), 30.0),
        (_ms(2026, 8, 5, 10, 30), 40.0),
    ]
    monkeypatch.setattr(quota, "_history_samples", lambda: history)

    class ConflictingDb:
        def quota_samples(self, provider=None):
            raise AssertionError("для недельной квоты Claude не нужен второй источник")

    assert quota.weekly_consumed(_period(), ConflictingDb(), 40.0) == 10.0


def test_weekly_consumed_falls_back_to_database_outside_history(monkeypatch):
    monkeypatch.setattr(
        quota,
        "_history_samples",
        lambda: [(_ms(2026, 8, 20, 9, 0), 90.0)],
    )

    class DatabaseFallback:
        def quota_samples(self, provider=None):
            assert provider == "claude"
            return [
                {"ts_utc": "2026-08-05T09:00:00+00:00", "weekly": 30.0},
                {"ts_utc": "2026-08-05T10:30:00+00:00", "weekly": 40.0},
            ]

    assert quota.weekly_consumed(_period(), DatabaseFallback(), None) == 10.0


def test_weekly_consumed_keeps_one_claude_source(monkeypatch):
    monkeypatch.setattr(quota, "_history_samples", lambda: [])
    mod = "claude-code:official-mod"
    statusline = "claude-code:statusline"

    class DatabaseFallback:
        def quota_samples(self, provider=None):
            assert provider == "claude"
            return [
                {"ts_utc": "2026-08-05T09:00:00+00:00", "weekly": 34.0, "source": mod},
                {"ts_utc": "2026-08-05T09:50:00+00:00", "weekly": 33.0, "source": statusline},
                {"ts_utc": "2026-08-05T10:30:00+00:00", "weekly": 47.0, "source": mod},
                {"ts_utc": "2026-08-05T11:00:00+00:00", "weekly": 42.0, "source": statusline},
                {"ts_utc": "2026-08-05T11:30:00+00:00", "weekly": 49.0, "source": mod},
            ]

    db = DatabaseFallback()
    assert quota.weekly_consumed(_period(), db, 42.0, current_source=statusline) == 15.0
    assert quota.weekly_consumed(_period(), db, 51.0, current_source=mod) == 17.0

    statusline_period = Period(
        "custom", "Тест",
        dt.datetime(2026, 8, 5, 10, 45, tzinfo=UTC),
        dt.datetime(2026, 8, 5, 11, 15, tzinfo=UTC),
    )
    assert quota.weekly_consumed(statusline_period, db, None) == 9.0


def test_history_samples_normalizes_second_timestamps(monkeypatch, tmp_path):
    path = tmp_path / "plan-usage-history.json"
    path.write_text(json.dumps({
        "samples": [{"t": int(dt.datetime(2026, 8, 5, 9, 0, tzinfo=UTC).timestamp()), "u": {"sd": 30}}]
    }), encoding="utf-8")
    monkeypatch.setattr(quota, "plan_history_path", lambda: path)

    assert quota._history_samples() == [(_ms(2026, 8, 5, 9, 0), 30.0)]


def test_history_samples_ignores_non_object_json(monkeypatch, tmp_path):
    path = tmp_path / "plan-usage-history.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(quota, "plan_history_path", lambda: path)

    assert quota._history_samples() == []


def test_weekly_consumed_monotonic(monkeypatch):
    samples = [
        (_ms(2026, 8, 5, 9, 0), 10.0),
        (_ms(2026, 8, 5, 10, 30), 15.0),
        (_ms(2026, 8, 5, 11, 30), 18.0),
    ]
    monkeypatch.setattr(quota, "_history_samples", lambda: samples)
    consumed = quota.weekly_consumed(_period(), None, current_weekly=18.0)
    assert abs(consumed - 8.0) < 1e-6  # 15->18 нарастающе, current без изменений


def test_parse_codex_manual_resets_zero():
    credits = quota.parse_codex_manual_resets({
        "result": {"rateLimits": {"rateLimitResetCredits": {
            "availableCount": 0,
            "credits": [],
        }}},
    })

    assert credits.state == "none"
    assert credits.available_count == 0
    assert credits.items == []
    assert credits.details_complete is True


@pytest.mark.parametrize("count", [1, 3])
def test_parse_codex_manual_resets_sorts_expiries(count):
    expires = [1_800_000_000 + offset for offset in range(count, 0, -1)]
    credits = quota.parse_codex_manual_resets({
        "rateLimitResetCredits": {
            "availableCount": count,
            "credits": [{
                "id": f"credit-{index}",
                "status": "available",
                "resetType": "codexRateLimits",
                "expiresAt": value,
            } for index, value in enumerate(expires)],
        },
    })

    assert credits.state == "available"
    assert credits.available_count == count
    assert [item.expires_at.timestamp() for item in credits.items] == sorted(expires)
    assert credits.details_complete is True


def test_parse_codex_manual_resets_missing_summary_is_unknown():
    credits = quota.parse_codex_manual_resets({"result": {"rateLimits": {}}})

    assert credits.state == "unknown"
    assert credits.available_count is None




def test_fetch_quota_codex_uses_10080_as_weekly(monkeypatch):
    _fake_app_server(monkeypatch)
    snap = quota.fetch_quota("codex")
    assert snap.source == "codex-app-server:account/rateLimits/read"
    assert snap.weekly == 24.0
    assert snap.five_hour is None
    assert snap.fable_weekly == 5.0
    assert snap.main_label == "Недельная квота"
    assert snap.extra_label == "GPT-5.3-Codex-Spark"


def test_fetch_quota_codex_keeps_reset_credits_out_of_quota_windows(monkeypatch):
    _fake_app_server(monkeypatch)
    snap = quota.fetch_quota("codex")
    assert snap.manual_resets.state == "available"
    assert snap.manual_resets.available_count == 2
    assert all(window.id != "reset-credits" for window in snap.windows)


def test_claude_does_not_reuse_old_account_native_history(monkeypatch, tmp_path):
    history = tmp_path / "plan-usage-history.json"
    sampled_at = dt.datetime.now(UTC) - dt.timedelta(minutes=1)
    history.write_text(json.dumps({"samples": [{
        "t": int(sampled_at.timestamp() * 1000),
        "u": {"fh": 6, "sd": 3},
    }]}), encoding="utf-8")
    monkeypatch.setattr(quota, "plan_history_path", lambda: history)
    monkeypatch.setattr(quota, "fetch_codex_quota_from_app_server", lambda: pytest.fail("Wrong provider"))
    monkeypatch.setattr(quota, "read_best_local_claude_quota",
                        lambda: quota.QuotaSnapshot(provider="claude", source="claude-code:statusline"))
    snap = quota.fetch_quota("claude")
    assert snap.source == "claude-code:statusline"
    assert snap.five_hour is None
    assert snap.weekly is None
    assert not snap.has_data()


def test_informational_main_window_is_not_selected():
    snapshot = quota.QuotaSnapshot(
        provider="antigravity",
        main_window_id="primary",
        windows=[
            quota.QuotaWindow("primary", "Нет данных", 0.0, informational=True),
            quota.QuotaWindow("model-a", "Gemini", 12.0, role="model"),
        ],
    )

    assert snapshot.main_window().id == "model-a"


def test_quota_consumed_isolated_provider_history(monkeypatch):
    monkeypatch.setattr(quota, "_history_samples", lambda: [(_ms(2026, 8, 5, 9, 0), 99.0)])
    class FakeDb:
        def quota_samples(self, provider=None):
            assert provider == "codex"
            return [
                {"ts_utc": "2026-08-05T09:00:00+00:00", "weekly": 10.0, "source": "codexbar:codex"},
                {"ts_utc": "2026-08-05T10:30:00+00:00", "weekly": 13.0, "source": "codexbar:codex"},
            ]
    assert quota.quota_consumed(_period(), FakeDb(), 15.0, "codex") == 5.0


def test_quota_consumed_does_not_invent_before_first_snapshot(monkeypatch):
    monkeypatch.setattr(quota, "_history_samples", lambda: [])
    class FakeDb:
        def quota_samples(self, provider=None):
            return [{"ts_utc": "2026-08-05T10:30:00+00:00", "weekly": 13.0, "source": f"codexbar:{provider}"}]
    assert quota.quota_consumed(_period(), FakeDb(), 13.0, "antigravity") is None





def test_quota_window_consumed_isolated_windows(monkeypatch):
    monkeypatch.setattr(quota, "_history_samples", lambda: [])
    from claude_usage_manager.models import QuotaWindow
    class FakeDb:
        def quota_samples(self, provider=None):
            assert provider == "antigravity"
            return [
                {"ts_utc": "2026-08-05T09:00:00+00:00", "windows": [QuotaWindow("model-a", "A", 10.0), QuotaWindow("model-b", "B", 50.0)]},
                {"ts_utc": "2026-08-05T10:30:00+00:00", "windows": [QuotaWindow("model-a", "A", 13.0), QuotaWindow("model-b", "B", 80.0)]},
            ]
    assert quota.quota_window_consumed(_period(), FakeDb(), 15.0, "antigravity", "model-a") == 5.0
    assert quota.quota_window_consumed(_period(), FakeDb(), 82.0, "antigravity", "model-b") == 32.0


_APP_SERVER_RATE_LIMITS = {
    "id": 2,
    "result": {
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 24, "windowDurationMins": 10080, "resetsAt": 1789805380},
            "secondary": None,
            "planType": "prolite",
        },
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex",
                "primary": {"usedPercent": 24, "windowDurationMins": 10080, "resetsAt": 1789805380},
                "planType": "prolite",
            },
            "codex_bengalfox": {
                "limitId": "codex_bengalfox",
                "limitName": "GPT-5.3-Codex-Spark",
                "primary": {"usedPercent": 3, "windowDurationMins": 300, "resetsAt": 1789242952},
                "secondary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": 1789829752},
                "planType": "prolite",
            },
        },
        "rateLimitResetCredits": {"availableCount": 2, "credits": []},
    },
}


def _fake_app_server(monkeypatch, payload=None):
    quota.reset_codex_rate_limits_cache()
    monkeypatch.setattr(quota, "_read_codex_rate_limits", lambda *a, **k: payload or _APP_SERVER_RATE_LIMITS)


def test_codex_app_server_windows_map_weekly_and_extra():
    windows, plan = quota.parse_codex_app_server_windows(_APP_SERVER_RATE_LIMITS)
    assert plan == "Pro Lite"
    assert [(w.id, w.used_percent, w.window_minutes, w.role) for w in windows] == [
        ("primary", 24.0, 10080, "primary"),
        ("gpt-5-3-codex-spark", 5.0, 10080, "extra"),
    ]


def test_codex_app_server_snapshot_is_live_and_real(monkeypatch):
    _fake_app_server(monkeypatch)
    snap = quota.fetch_codex_quota_from_app_server()
    assert snap.source == "codex-app-server:account/rateLimits/read"
    assert snap.error == ""
    assert snap.has_data()
    assert snap.weekly == 24.0
    assert snap.main_window_id == "primary"
    assert snap.main_label == "Недельная квота"
    assert snap.fable_weekly == 5.0
    assert snap.login_method == "Pro Lite"
    assert snap.updated_at is not None
    assert snap.manual_resets.available_count == 2


def test_fetch_quota_codex_uses_only_native(monkeypatch):
    _fake_app_server(monkeypatch)
    monkeypatch.setattr(quota.subprocess, "run", lambda *args, **kwargs: pytest.fail("Unexpected external CLI"))
    snap = quota.fetch_quota("codex")
    assert snap.source == "codex-app-server:account/rateLimits/read"
    assert snap.weekly == 24.0
    assert snap.has_data()


def test_fetch_quota_codex_does_not_fallback_to_external_adapter(monkeypatch):
    monkeypatch.setattr(quota, "fetch_codex_quota_from_app_server",
                        lambda: quota.QuotaSnapshot(provider="codex", source="codex-app-server:account/rateLimits/read", error="native unavailable"))
    monkeypatch.setattr(quota.subprocess, "run", lambda *args, **kwargs: pytest.fail("Unexpected external CLI"))
    snap = quota.fetch_quota("codex")
    assert snap.source == "codex-app-server:account/rateLimits/read"
    assert not snap.has_data()
    assert snap.error == "native unavailable"


def test_fetch_quota_codex_reports_native_error(monkeypatch):
    quota.reset_codex_rate_limits_cache()
    monkeypatch.setattr(quota, "_read_codex_rate_limits", lambda *a, **k: {"result": {}})
    snap = quota.fetch_quota("codex")
    assert snap.source == "codex-app-server:account/rateLimits/read"
    assert not snap.has_data()
    assert "не вернул окна лимитов" in snap.error


def test_codex_rate_limits_cache_serves_one_call_per_cycle(monkeypatch):
    quota.reset_codex_rate_limits_cache()
    calls = {"n": 0}
    def _count(*a, **k):
        calls["n"] += 1
        return _APP_SERVER_RATE_LIMITS
    monkeypatch.setattr(quota, "_read_codex_rate_limits_uncached", _count)
    quota._read_codex_rate_limits()
    quota._read_codex_rate_limits()
    assert calls["n"] == 1
    quota.reset_codex_rate_limits_cache()
    quota._read_codex_rate_limits()
    assert calls["n"] == 2

def test_claude_quota_uses_only_offline_statusline(monkeypatch, tmp_path):
    from claude_usage_manager.claude_local import ingest_statusline
    stamp = dt.datetime.now(UTC)
    path = tmp_path / "status.json"
    assert ingest_statusline({
        "rate_limits": {"five_hour": {
            "used_percentage": 25,
            "resets_at": (stamp + dt.timedelta(hours=1)).timestamp(),
        }}
    }, path=path, now=stamp)
    monkeypatch.setattr(quota, "fetch_codex_quota_from_app_server", lambda: (
        pytest.fail("Claude must not use Codex app-server")))
    monkeypatch.setattr(quota, "read_best_local_claude_quota", lambda: __import__(
        "claude_usage_manager.claude_local", fromlist=["read_statusline_quota"]
    ).read_statusline_quota(path=path, now=stamp))
    snapshot = quota.fetch_quota("claude")
    assert snapshot.five_hour == 25
    assert snapshot.source == "claude-code:statusline"


def test_claude_web_session_and_private_offer_probes_removed():
    for name in (
        "fetch_claude_manual_resets", "parse_claude_manual_resets",
        "normalize_claude_web_session", "save_claude_web_session",
        "_claude_web_usage_probe", "_claude_web_session", "_claude_org_uuid",
    ):
        assert not hasattr(quota, name)
