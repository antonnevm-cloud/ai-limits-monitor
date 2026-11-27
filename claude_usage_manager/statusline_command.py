"""Команда запуска легкого обработчика строки состояния из официального клиента."""
from __future__ import annotations

import base64
import os
import re
import shlex
import shutil
import sys
from pathlib import Path


def _monitor_program() -> list[str]:
    # На macOS python в виртуальном окружении часто сам является ссылкой.
    interpreter = Path(sys.executable).absolute()
    entry = Path(sys.argv[0]).resolve()
    if getattr(sys, "frozen", False):
        return [interpreter.as_posix()]
    if "__compiled__" in globals() or entry.name.casefold() in {"ai limits monitor.exe", "claudeusagemanager.exe"}:
        return [entry.as_posix()]
    if interpreter.name.casefold() == "pythonw.exe":
        interpreter = interpreter.with_name("python.exe")
    if not interpreter.is_file():
        raise FileNotFoundError("Консольный Python для строки состояния не найден.")
    script = Path(__file__).resolve().parents[1] / "monitor.py"
    return [interpreter.as_posix(), script.as_posix()]


def _git_bash_installed() -> bool:
    override = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if override:
        return Path(override).is_file()
    git = shutil.which("git.exe")
    roots = [Path(git).parent.parent] if git else []
    if os.environ.get("ProgramFiles"):
        roots.append(Path(os.environ["ProgramFiles"]) / "Git")
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "Programs/Git")
    return any((root / "bin/bash.exe").is_file() for root in roots)


def _system_powershell() -> str:
    import ctypes

    directory = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(directory, len(directory))
    if not 0 < length < len(directory):
        raise OSError("Не удалось найти системный каталог Windows.")
    program = Path(directory.value) / "WindowsPowerShell/v1.0/powershell.exe"
    if not program.is_file():
        raise FileNotFoundError("Системный Windows PowerShell не найден.")
    return program.as_posix()


def statusline_command(flag: str, *arguments: str) -> str:
    program = _monitor_program()
    args = [flag, *arguments]
    if os.name != "nt":
        paths = [shlex.quote(path) if any(c in path for c in ('"', "$", chr(96), "\\")) else f'"{path}"'
                 for path in program]
        return " ".join(paths + [shlex.quote(arg) for arg in args])

    command_line = program + args
    # agy использует cmd, Claude Code выбирает Git Bash либо PowerShell.
    uses_cmd = flag == "--antigravity-statusline"
    uses_bash = not uses_cmd and _git_bash_installed()
    special = any(c in value for value in command_line for c in ('%', '!', '"', '$', '^', '\\', chr(96), '\r', '\n'))
    if special or not (uses_cmd or uses_bash):
        literals = ["'" + value.replace("'", "''") + "'" for value in command_line]
        # Программу без консоли PowerShell не ждет, если она последняя в конвейере:
        # вывод приходит после выхода оболочки, код завершения теряется. ForEach-Object возвращает ожидание
        # и входит в ядро PowerShell, поэтому не пишет в stderr сообщение о подготовке модулей.
        script = "& " + " ".join(literals) + " | ForEach-Object { $_ }; exit $LASTEXITCODE"
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        shell = _system_powershell()
        if not re.fullmatch(r"[A-Za-z0-9:/_.-]+", shell):
            if uses_cmd:
                shell = f'call "{shell}"'
            elif uses_bash:
                shell = shlex.quote(shell)
            else:
                shell = "& '" + shell.replace("'", "''") + "'"
        return shell + " -NoProfile -NonInteractive -EncodedCommand " + encoded
    words = [f'"{path}"' for path in program]
    words.extend(arg if re.fullmatch(r"[A-Za-z0-9_-]+", arg) else f'"{arg}"' for arg in args)
    # CALL сохраняет кавычки вокруг первого пути в cmd /c.
    return ("call " if uses_cmd else "") + " ".join(words)
