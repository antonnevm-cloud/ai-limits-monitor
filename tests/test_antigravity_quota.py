import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from claude_usage_manager import antigravity_quota as ag
from claude_usage_manager import provider_settings as settings
from claude_usage_manager import statusline_command as commands


NOW = dt.datetime(2026, 11, 26, 12, tzinfo=dt.timezone.utc)
RESET = NOW + dt.timedelta(days=5)


@pytest.fixture(autouse=True)
def private_data(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LIMITS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(ag, "_now", lambda: NOW)
    monkeypatch.setattr(settings, "load_connections", lambda: {"antigravity": settings.Connection()})


def native_report():
    return {"status": "SUCCESS", "num_turns": 0, "command": {"name": "usage", "data": {"groups": [
        {"name": "Gemini Models", "buckets": [
            {"id": "gemini-weekly", "remaining_fraction": .25, "reset_time": RESET.isoformat()},
            {"id": "gemini-5h", "remaining_fraction": 0, "reset_in_seconds": 60},
        ]},
        {"name": "Claude and GPT models", "buckets": [
            {"id": "3p-weekly", "remaining_fraction": 1, "reset_time": "1970-01-01T00:00:00Z"},
            {"id": "3p-5h", "remaining_fraction": 1},
        ]},
    ]}}}


def test_native_reads_public_report_and_preserves_zero_and_unknown_reset(monkeypatch, tmp_path):
    exe = tmp_path / "client with spaces" / "agy"
    monkeypatch.setattr(settings, "provider_executable", lambda provider, connection: exe)
    calls = []

    def run(args, **options):
        calls.append(args)
        assert options["stdin"] == subprocess.DEVNULL
        assert options["stdout"] == subprocess.PIPE and options["stderr"] == subprocess.DEVNULL
        assert options.get("shell", False) is False
        value = b"1.1.11\n" if args[1:] == ["--version"] else json.dumps(native_report()).encode()
        return subprocess.CompletedProcess(args, 0, value, b"")

    monkeypatch.setattr(ag, "run_hidden", run)
    snap = ag.fetch_antigravity_quota()
    assert calls == [[str(exe), "--version"], [str(exe), "--print", "/usage", "--output-format", "json"]]
    assert snap.source == ag.CLI_SOURCE and not snap.error and not snap.fallback_source
    assert snap.updated_at == NOW and snap.weekly == 75 and snap.five_hour == 100
    assert snap.weekly_resets_at == RESET and snap.five_hour_resets_at == NOW + dt.timedelta(minutes=1)
    assert snap.main_window_id == "ag-group:gemini:weekly"
    assert all(w.role == "group" for w in snap.windows)
    assert snap.window("ag-group:claude-gpt:weekly").used_percent == 0
    assert snap.window("ag-group:claude-gpt:weekly").resets_at is None


@pytest.mark.parametrize("version", [b"1.1.10", b"1.1.11-rc1", b"unknown", b"failed: using 1.8.0"])
def test_unsupported_version_never_sends_print(monkeypatch, tmp_path, version):
    calls = []

    def run(args, **options):
        calls.append(args[1:])
        return subprocess.CompletedProcess(args, 0, version, b"")

    monkeypatch.setattr(ag, "run_hidden", run)
    snap = ag._native_usage(tmp_path / "agy")
    assert calls == [["--version"]]
    assert not snap.has_data() and "1.1.11" in snap.error


@pytest.mark.parametrize("reply", [
    {"status": "SUCCESS", "num_turns": 1, "command": native_report()["command"]},
    {"status": "SUCCESS", "num_turns": 0, "command": {"name": "models", "data": {"groups": []}}},
    {"status": "ERROR", "num_turns": 0, "command": native_report()["command"]},
    {"response": "Gemini weekly: 100%", "status": "SUCCESS", "num_turns": 0},
])
def test_accepts_only_structured_native_command(monkeypatch, tmp_path, reply):
    def run(args, **options):
        data = b"1.5.18" if args[-1] == "--version" else json.dumps(reply).encode()
        return subprocess.CompletedProcess(args, 0, data, b"")

    monkeypatch.setattr(ag, "run_hidden", run)
    snap = ag._native_usage(tmp_path / "agy")
    assert not snap.has_data() and snap.error


def test_clean_installation_needs_no_client_files(monkeypatch):
    monkeypatch.setattr(settings, "provider_executable", lambda *args: None)
    monkeypatch.setattr(ag, "run_hidden", lambda *a, **kw: pytest.fail("unexpected process"))
    snap = ag.fetch_antigravity_quota()
    assert not snap.has_data() and snap.updated_at is None
    assert "agy" in snap.error and "строку состояния" in snap.error


def test_noninteractive_failure_can_use_recent_public_statusline(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "provider_executable", lambda *args: tmp_path / "agy")
    monkeypatch.setattr(ag, "run_hidden", lambda args, **kw: subprocess.CompletedProcess(
        args, 0 if "--version" in args else 1, b"1.5.18" if "--version" in args else b"", b"personal-info",
    ))
    key = ag._profile_key(settings.provider_profile("antigravity"), settings.antigravity_cli_profile())
    assert ag._ingest({"product": "antigravity", "quota": {"gemini-weekly": {"remaining_fraction": .7}}}, key, NOW)
    result = ag.fetch_antigravity_quota()
    assert result.source == ag.STATUSLINE_SOURCE and result.weekly == pytest.approx(30)
    assert not result.fallback_source and not result.error


def test_statusline_preserves_only_quota_and_expires_each_window(tmp_path):
    key = ag._profile_key(tmp_path / "app", tmp_path / "cli")
    payload = {"product": "antigravity", "email": "must-not-persist", "conversation_title": "private", "quota": {
        "gemini-weekly": {"remaining_fraction": .21, "reset_time": RESET.isoformat(), "private": "discard"},
        "gemini-5h": {"remaining_fraction": 0, "reset_in_seconds": 1},
        "3p-weekly": {"remaining_amount": 890},
        "3p-5h": {"remaining_fraction": .8, "disabled": True},
        "spent-weekly": {"remaining_fraction": .6, "reset_time": (NOW - dt.timedelta(hours=1)).isoformat()},
    }}
    assert ag._ingest(payload, key, NOW)
    private_sample = json.loads(ag._sample_path(key).read_bytes())
    assert private_sample == {"received_at": NOW.isoformat(), "quota": {
        "gemini-weekly": {"remaining_fraction": .21, "reset_time": RESET.isoformat()},
        "gemini-5h": {"remaining_fraction": 0, "reset_time": (NOW + dt.timedelta(seconds=1)).isoformat()},
    }}
    fresh = ag._statusline_sample(key, NOW)
    assert fresh.weekly == 79 and fresh.five_hour == 100 and not fresh.fallback_source
    after_reset = ag._statusline_sample(key, NOW + dt.timedelta(seconds=1))
    assert after_reset.five_hour is None and after_reset.weekly == 79
    older = ag._statusline_sample(key, NOW + ag.FRESH_FOR + dt.timedelta(seconds=1))
    assert older.updated_at == NOW and older.fallback_source and older.weekly == 79
    assert not ag._statusline_sample(key, NOW + ag.MAX_AGE + dt.timedelta(seconds=1)).has_data()
    assert not ag._statusline_sample(key, NOW - dt.timedelta(seconds=1)).has_data()


@pytest.mark.parametrize("bucket", [
    {"remaining_fraction": 1.2}, {"remaining_fraction": True}, {"remaining_fraction": float("nan")},
    {"remaining_fraction": .2, "reset_time": "bad"},
    {"remaining_fraction": 1, "reset_time": "2026-12-01T00:00:00"},
    {"remaining_fraction": .2, "reset_in_seconds": -1},
    {"remaining_fraction": .2, "reset_in_seconds": 10**200},
    {"remaining_amount": 25}, {"remaining_fraction": .4, "disabled": True},
])
def test_invalid_or_unusable_statusline_bucket_does_not_revive_old_value(tmp_path, bucket):
    key = ag._profile_key(tmp_path, tmp_path / "cli")
    good = {"product": "antigravity", "quota": {"gemini-weekly": {"remaining_fraction": .2}}}
    assert ag._ingest(good, key, NOW)
    assert not ag._ingest({"product": "antigravity", "quota": {"gemini-weekly": bucket}}, key, NOW)
    assert not ag._statusline_sample(key, NOW).has_data()


def test_receipt_stays_with_its_installed_profile_after_monitor_switch(monkeypatch, tmp_path):
    first = settings.Connection(profile=str(tmp_path / "a"), cli_profile=str(tmp_path / "cli-a"))
    second = settings.Connection(profile=str(tmp_path / "b"), cli_profile=str(tmp_path / "cli-b"))
    monkeypatch.setattr(settings, "load_connections", lambda: {"antigravity": second})
    monkeypatch.setattr(settings, "provider_executable", lambda *a: pytest.fail("wrong profile"))
    old_key = ag._profile_key(Path(first.profile), Path(first.cli_profile))
    packet = {"product": "antigravity", "quota": {"3p-weekly": {"remaining_fraction": .25}}}
    monkeypatch.setattr(sys, "stdin", io.BytesIO(json.dumps(packet).encode()))
    assert ag.antigravity_statusline_main([old_key]) == 0
    assert not ag.fetch_antigravity_quota().has_data()
    monkeypatch.setattr(settings, "load_connections", lambda: {"antigravity": first})
    result = ag.fetch_antigravity_quota()
    assert result.weekly == 75 and result.source == ag.STATUSLINE_SOURCE
    assert result.main_window_id == "ag-group:claude-gpt:weekly"


@pytest.mark.parametrize("current", [{"type": "command", "command": "my-command"},
                                    {"enabled": False}, {"type": "other"}, False, None])
def test_explicit_install_preserves_existing_setting_byte_for_byte(tmp_path, current):
    path = tmp_path / "settings.json"
    original = json.dumps({"theme": "custom", "statusLine": current}, ensure_ascii=True).encode("utf-8-sig")
    path.write_bytes(original)
    assert ag.install_antigravity_statusline(profile=tmp_path / "history", cli_profile=tmp_path) == "conflict"
    assert path.read_bytes() == original


@pytest.mark.parametrize("destination", ["data", "private %TEMP% ! & 'account'"])
def test_installed_command_receives_official_stdin_via_system_shell(tmp_path, monkeypatch, destination):
    monkeypatch.setenv("AI_LIMITS_DATA_DIR", str(tmp_path / destination))
    profile = tmp_path / "history"
    cli = tmp_path / "my agy profile"
    cli.mkdir()
    config = cli / "settings.json"
    config.write_text('{"theme":"dark","statusLine":{"padding":2}}', encoding="utf-8")
    assert ag.install_antigravity_statusline(profile=profile, cli_profile=cli) == "installed"
    saved = config.read_bytes()
    assert ag.install_antigravity_statusline(profile=profile, cli_profile=cli) == "unchanged"
    assert saved == config.read_bytes()
    obj = json.loads(saved)
    assert obj["theme"] == "dark" and obj["statusLine"]["padding"] == 2
    assert obj["statusLine"]["stack_with_default"] is True
    command = obj["statusLine"]["command"]
    packet = {"product": "antigravity", "quota": {"gemini-weekly": {"remaining_fraction": .35}}}
    with monkeypatch.context() as child:
        child.setenv("AI_LIMITS_DATA_DIR", str(tmp_path / "different-terminal"))
        ran = subprocess.run(command, shell=True, cwd=tmp_path, input=json.dumps(packet).encode(), capture_output=True, timeout=5)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, b"", b"")
    key = ag._profile_key(profile, cli)
    result = ag._statusline_sample(key, dt.datetime.now(dt.timezone.utc))
    assert result.weekly == 65


def test_installer_moves_only_the_command_it_installed(tmp_path, monkeypatch):
    cli = tmp_path / "cli"
    monkeypatch.setattr(ag, "_bridge_command", lambda key: "app-v1 --antigravity-statusline " + key)
    assert ag.install_antigravity_statusline(profile=tmp_path / "history", cli_profile=cli) == "installed"
    monkeypatch.setattr(ag, "_bridge_command", lambda key: "app-v2 --antigravity-statusline " + key)
    assert ag.install_antigravity_statusline(profile=tmp_path / "history", cli_profile=cli) == "installed"
    config = cli / "settings.json"
    raw = json.loads(config.read_bytes())
    raw["statusLine"]["command"] += " custom"
    config.write_text(json.dumps(raw), encoding="utf-8")
    assert ag.install_antigravity_statusline(profile=tmp_path / "different", cli_profile=cli) == "conflict"
    assert json.loads(config.read_bytes()) == raw


def test_invalid_cli_args_and_unrelated_payload_do_not_create_file(tmp_path, monkeypatch):
    key = ag._profile_key(tmp_path, tmp_path)
    assert ag.antigravity_statusline_main(["../other"]) != 0
    assert ag.antigravity_statusline_main([key, "../other"]) != 0
    for packet in [b"broken", b'{"product":"another","quota":{}}', b'{"product":"antigravity"}', b"X" * 1_000_001]:
        monkeypatch.setattr(sys, "stdin", io.BytesIO(packet))
        assert ag.antigravity_statusline_main([key]) == 0
    assert not ag._sample_path(key).exists()


@pytest.mark.parametrize("client,special", [
    ("agy", False), ("agy", True), ("claude", False), ("claude", True), ("git-bash", False), ("git-bash", True),
])
def test_shared_statusline_command_preserves_arguments_in_client_shell(tmp_path, monkeypatch, client, special):
    script = tmp_path / "read argv.py"
    script.write_text("import sys, json\nprint(json.dumps([sys.argv[1:], sys.stdin.read()]))\n", encoding="utf-8")
    monkeypatch.setattr(commands, "_monitor_program", lambda: [Path(sys.executable).as_posix(), script.as_posix()])
    arg = "folder %TEMP% ! $ 'case' &" if special else "folder (one) & two"
    flag = "--antigravity-statusline" if client == "agy" else "--claude-statusline"
    if client == "git-bash":
        git = shutil.which("git.exe") if os.name == "nt" else None
        bash = Path(git).parent.parent / "bin/bash.exe" if git else None
        if bash is None or not bash.is_file():
            pytest.skip("Git Bash для Windows отсутствует")
        monkeypatch.setattr(commands, "_git_bash_installed", lambda: True)
        cmd = [str(bash), "--noprofile", "--norc", "-c", commands.statusline_command(flag, arg)]
    elif os.name == "nt" and client == "claude":
        # При отсутствии Git Bash Claude Code исполняет команду через PowerShell.
        monkeypatch.setattr(commands, "_git_bash_installed", lambda: False)
        cmd = [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", commands.statusline_command(flag, arg)]
    elif os.name == "nt":
        cmd = commands.statusline_command(flag, arg)
    else:
        cmd = ["sh", "-c", commands.statusline_command(flag, arg)]
    result = subprocess.run(cmd, shell=isinstance(cmd, str), input=b"stdin-check", capture_output=True, timeout=5)
    assert result.returncode == 0
    assert json.loads(result.stdout) == [[flag, arg], "stdin-check"]
