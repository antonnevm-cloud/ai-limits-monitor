"""Обмен с Codex app-server на имитаторе процесса."""
import datetime as dt
import json
import queue
import subprocess

import pytest

from claude_usage_manager import quota


class _Stdout:
    def __init__(self):
        self.lines = queue.Queue()

    def readline(self):
        return self.lines.get(timeout=10)


class _Stdin:
    def __init__(self, process):
        self.process = process
        self.buffer = ""

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.process.received(json.loads(line))

    def flush(self):
        pass


class FakeAppServer:
    """Процесс app-server: отвечает по сценарию, завершается по terminate, если не упрямый."""

    pid = None

    def __init__(self, answer, *, stubborn=False):
        self.answer = answer
        self.stubborn = stubborn
        self.sent = []
        self.returncode = None
        self.terminated = 0
        self.killed = 0
        self.stdin = _Stdin(self)
        self.stdout = _Stdout()

    def received(self, message):
        self.sent.append(message)
        for reply in self.answer(message) or ():
            if reply is None:
                self.stdout.lines.put("")  # процесс закрыл вывод
            else:
                self.stdout.lines.put(json.dumps(reply) + "\n")

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        if not self.stubborn:
            self.returncode = 0
            self.stdout.lines.put("")

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("codex", timeout)
        return self.returncode

    def kill(self):
        self.killed += 1
        self.returncode = -9
        self.stdout.lines.put("")


RESET = int(dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc).timestamp())
LIMITS = {
    "rateLimits": {
        "limitId": "codex",
        "planType": "pro",
        "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": RESET},
        "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": RESET},
    },
    "rateLimitResetCredits": {
        "availableCount": 1,
        "credits": [
            {"id": "a", "status": "available", "expiresAt": "2026-12-20T00:00:00Z"},
            {"id": "b", "status": "redeemed", "expiresAt": "2026-12-05T00:00:00Z"},
        ],
    },
}


def _normal(message):
    if message.get("method") == "initialize":
        return [{"id": 1, "result": {"userAgent": "codex"}}]
    if message.get("method") == "account/rateLimits/read":
        return [{"method": "notice"}, {"id": 2, "result": LIMITS}]
    return []


@pytest.fixture
def launch(monkeypatch, tmp_path):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"")
    monkeypatch.setattr(quota, "codex_cli", lambda: executable)
    started = []

    def factory(answer, **options):
        def popen(command, **kwargs):
            assert command[1:] == ["app-server", "--listen", "stdio://"]
            process = FakeAppServer(answer, **options)
            started.append(process)
            return process

        monkeypatch.setattr(quota.subprocess, "Popen", popen)
        return started

    quota.reset_codex_rate_limits_cache()
    yield factory
    quota.reset_codex_rate_limits_cache()


def test_conversation_reads_both_windows_and_stops_the_process(launch):
    started = launch(_normal)
    snapshot = quota.fetch_codex_quota_from_app_server(timeout=5)
    process = started[0]
    assert [m.get("method") for m in process.sent] == ["initialize", "initialized", "account/rateLimits/read"]
    assert process.terminated == 1 and process.killed == 0
    assert not snapshot.error
    titles = {window.id: window.title for window in snapshot.windows}
    assert titles == {"primary": "Лимит 5 часов", "secondary": "Недельная квота"}
    # Главным считается недельное окно, даже когда оно пришло вторым.
    assert snapshot.main_window_id == "secondary" and snapshot.weekly == 40
    assert snapshot.login_method


def test_rpc_error_becomes_readable_message(launch):
    def failing(message):
        if message.get("method") == "initialize":
            return [{"id": 1, "result": {}}]
        if message.get("id") == 2:
            return [{"id": 2, "error": {"code": -32600, "message": "not logged in"}}]
        return []

    launch(failing)
    snapshot = quota.fetch_codex_quota_from_app_server(timeout=5)
    assert "код -32600" in snapshot.error and "not logged in" in snapshot.error


def test_process_exit_without_answer_is_reported(launch):
    launch(lambda message: [None] if message.get("method") == "initialize" else [])
    snapshot = quota.fetch_codex_quota_from_app_server(timeout=5)
    assert "завершился без ответа" in snapshot.error


def test_silent_stubborn_process_times_out_and_is_killed(launch):
    started = launch(lambda message: [], stubborn=True)
    snapshot = quota.fetch_codex_quota_from_app_server(timeout=0.3)
    assert "не ответил вовремя" in snapshot.error
    process = started[0]
    assert process.terminated == 1 and process.killed == 1


def test_one_refresh_starts_one_process_for_windows_and_credits(launch):
    started = launch(_normal)
    quota.fetch_codex_quota_from_app_server(timeout=5)
    credits = quota.fetch_codex_manual_resets(timeout=5)
    assert len(started) == 1
    assert credits.available_count == 1
    assert [item.id for item in credits.items] == ["b", "a"]  # по сроку
    # Погашенный кредит не считается ближайшим сроком.
    assert credits.nearest_expiry(dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)).date() == dt.date(2026, 12, 20)


@pytest.mark.parametrize("value, expected", [
    (RESET, dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)),
    (RESET * 1000, dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)),
    (str(RESET), dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)),
    ("2026-12-01T00:00:00Z", dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)),
    ("не дата", None),
    (None, None),
])
def test_codex_timestamps(value, expected):
    assert quota._codex_timestamp(value) == expected
