"""Точка входа программы Windows без консоли: потоки вызывающего процесса и сбои без окна с ошибкой."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import monitor
from claude_usage_manager import APP_NAME, APP_VERSION, antigravity_quota, claude_local
from claude_usage_manager import statusline_command as commands

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 23}}}).encode()


def test_statusline_handlers_survive_missing_standard_streams(monkeypatch, tmp_path):
    # Так выглядит запуск программы без консоли, которой вызывающий процесс не передал потоки.
    monkeypatch.setattr(sys, "stdin", None)
    monkeypatch.setattr(sys, "stdout", None)
    assert claude_local.statusline_main([]) == 0
    assert antigravity_quota.antigravity_statusline_main([antigravity_quota._profile_key(tmp_path, tmp_path)]) == 0
    assert not claude_local._snapshot_path("").exists()


def test_entry_point_reports_failure_without_raising(monkeypatch, capsys):
    def broken(args):
        raise RuntimeError("сбой служебного режима")

    monkeypatch.setattr(monitor, "_run", broken)
    assert monitor.main() == 1
    assert "сбой служебного режима" in capsys.readouterr().err
    monkeypatch.setattr(sys, "stderr", None)
    assert monitor.main() == 1


def test_parent_console_is_used_only_without_inherited_stream(monkeypatch):
    inherited = sys.stdout
    monitor._use_parent_console()
    assert sys.stdout is inherited
    if sys.platform == "win32":
        import ctypes

        monkeypatch.setattr(ctypes.windll.kernel32, "AttachConsole", lambda process: 0)
        monkeypatch.setattr(sys, "stdout", None)
        monitor._use_parent_console()  # терминала запуска нет: программа открыта по ярлыку
        assert sys.stdout is None


def test_version_mode_writes_to_inherited_stream():
    result = subprocess.run([sys.executable, str(ROOT / "monitor.py"), "--version"],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert (result.returncode, result.stdout.strip()) == (0, f"{APP_NAME} {APP_VERSION}")


def _windowless_python():
    program = Path(sys.executable).with_name("pythonw.exe")
    return program if os.name == "nt" and program.is_file() else None


@pytest.mark.skipif(_windowless_python() is None, reason="Нужен pythonw.exe: программа Windows без консоли")
@pytest.mark.parametrize("client", ["powershell", "git-bash", "cmd"])
def test_windowless_program_keeps_streams_and_exit_code_in_client_shell(client, monkeypatch, tmp_path):
    """Собранный EXE не имеет консоли, как pythonw.exe: оболочка клиента обязана дождаться его и вернуть код."""
    program = [_windowless_python().as_posix(), (ROOT / "monitor.py").as_posix()]
    monkeypatch.setattr(commands, "_monitor_program", lambda: program)
    monkeypatch.setattr(commands, "_git_bash_installed", lambda: client == "git-bash")
    if client == "cmd":
        key = antigravity_quota._profile_key(tmp_path, tmp_path)
        good = commands.statusline_command("--antigravity-statusline", key)
        bad = commands.statusline_command("--antigravity-statusline", "not-a-key")
        packet = json.dumps({"product": "antigravity", "quota": {"gemini-weekly": {"remaining_fraction": 0.5}}}).encode()
        saved = antigravity_quota._sample_path(key)
    else:
        good = commands.statusline_command("--claude-statusline")
        bad = commands.statusline_command("--claude-statusline", "not-a-key")
        packet, saved = PAYLOAD, claude_local._snapshot_path("")

    def run(command):
        if client == "git-bash":
            git = shutil.which("git.exe")
            bash = Path(git).parent.parent / "bin/bash.exe" if git else None
            if bash is None or not bash.is_file():
                pytest.skip("Git Bash для Windows отсутствует")
            command = [str(bash), "--noprofile", "--norc", "-c", command]
        elif client == "powershell":
            command = [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", command]
        return subprocess.run(command, shell=isinstance(command, str), input=packet, capture_output=True, timeout=60)

    result = run(good)
    assert (result.returncode, result.stderr) == (0, b"")
    assert saved.is_file()
    if client != "cmd":
        assert b"Claude | 5h: 23%" in result.stdout
    # Внешний PowerShell с параметром -Command сводит любой отказ вложенной команды к коду 1.
    assert run(bad).returncode == (1 if client == "powershell" else 2)
