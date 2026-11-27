import datetime as dt
import json
import urllib.request
from types import SimpleNamespace

import pytest

from claude_usage_manager.local_api import DEFAULT_PORT, LOOPBACK_HOST, LocalApiServer, snapshot_payload
from claude_usage_manager.models import (
    ManualResetCredits,
    ManualResetItem,
    QuotaSnapshot,
    QuotaWindow,
)


UTC = dt.timezone.utc


def test_default_endpoint_contract():
    server = LocalApiServer(lambda: {})

    assert server.address == (LOOPBACK_HOST, DEFAULT_PORT)
    assert server.base_url == "http://127.0.0.1:28765"


def _state(provider: str, count: int):
    now = dt.datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    items = [
        ManualResetItem(
            id=f"credit-{index}",
            provider=provider,
            expires_at=now + dt.timedelta(hours=index + 1),
            description="SECRET_DESCRIPTION",
        )
        for index in reversed(range(count))
    ]
    snapshot = QuotaSnapshot(
        provider=provider,
        source=f"codexbar:{provider}",
        error="SECRET_TOKEN",
        updated_at=now,
        windows=[QuotaWindow(
            "primary",
            "Основной лимит",
            25,
            resets_at=now + dt.timedelta(days=7),
            window_minutes=10080,
            role="primary",
        )],
        main_window_id="primary",
        manual_resets=ManualResetCredits(
            provider=provider,
            state="none" if count == 0 else "available",
            available_count=count,
            items=items,
            source=f"{provider}-structured-source",
            freshness="live",
            updated_at=now,
            details_complete=True,
        ),
    )
    return SimpleNamespace(snapshot=snapshot, live=True, error="SECRET_COOKIE")


@pytest.mark.parametrize("provider", ["codex", "cursor"])
@pytest.mark.parametrize("count", [0, 1, 3])
def test_snapshot_supports_zero_one_many_manual_resets(provider, count):
    now = dt.datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    payload = snapshot_payload({provider: _state(provider, count)}, now=now)
    result = payload["providers"][provider]
    manual = result["manual_resets"]

    assert manual["available_count"] == count
    assert manual["state"] == ("none" if count == 0 else "available")
    assert len(manual["items"]) == count
    assert [item["remaining_seconds"] for item in manual["items"]] == sorted(
        item["remaining_seconds"] for item in manual["items"]
    )
    assert all(window["id"] != "reset-credits" for window in result["quota_windows"])


def test_snapshot_unknown_is_not_zero_and_omits_secrets():
    state = _state("cursor", 0)
    state.snapshot.manual_resets = ManualResetCredits(
        provider="cursor",
        state="unknown",
        source="cursor:DashboardService",
        error="SECRET_MANUAL_ERROR",
    )

    payload = snapshot_payload({"cursor": state})
    manual = payload["providers"]["cursor"]["manual_resets"]
    encoded = json.dumps(payload)

    assert manual["state"] == "unknown"
    assert manual["available_count"] is None
    assert "SECRET" not in encoded
    assert "error" not in payload["providers"]["cursor"]


def test_loopback_http_endpoints_and_refresh():
    calls = []
    server = LocalApiServer(
        lambda: {"codex": _state("codex", 1)},
        lambda: calls.append("refresh"),
        port=0,
    )
    server.start()
    try:
        assert server.address[0] == "127.0.0.1"
        with urllib.request.urlopen(f"{server.base_url}/health", timeout=3) as response:
            health = json.load(response)
        with urllib.request.urlopen(f"{server.base_url}/v1/snapshot", timeout=3) as response:
            snapshot = json.load(response)
        request = urllib.request.Request(
            f"{server.base_url}/v1/refresh",
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            refresh = json.load(response)

        assert health == {
            "schema_version": 1,
            "service": "ai-limits-monitor",
            "status": "ok",
        }
        assert "codex" in snapshot["providers"]
        assert refresh == {"accepted": True}
        assert calls == ["refresh"]
    finally:
        server.stop()


def test_local_api_rejects_non_loopback_binding():
    with pytest.raises(ValueError, match="127.0.0.1"):
        LocalApiServer(lambda: {}, host="0.0.0.0")
