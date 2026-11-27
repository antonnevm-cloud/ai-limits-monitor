"""Автозапуск при входе в Windows штатным способом: параметр в разделе Run профиля пользователя.

Тот же параметр пишет флажок установщика, удаление программы его убирает.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = APP_NAME
FLAG = "--autostart"


def supported() -> bool:
    return sys.platform == "win32"


def command() -> str:
    """Команда запуска текущей программы: собранный EXE либо pythonw с monitor.py."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" {FLAG}'
    interpreter = Path(sys.executable).resolve()
    windowless = interpreter.with_name("pythonw.exe")
    script = Path(__file__).resolve().parents[1] / "monitor.py"
    return f'"{windowless if windowless.is_file() else interpreter}" "{script}" {FLAG}'


def _program(value: str) -> str:
    value = value.strip()
    if value.startswith('"'):
        return value[1:].split('"', 1)[0]
    return value.split(" ", 1)[0]


def registered() -> str | None:
    if not supported():
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, kind = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return None
    return value if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str) else None


def is_enabled() -> bool:
    """Включен ли автозапуск именно этой копии программы."""
    value = registered()
    if not value:
        return False
    return os.path.normcase(os.path.normpath(_program(value))) == os.path.normcase(
        os.path.normpath(_program(command())))


def set_enabled(enabled: bool) -> None:
    if not supported():
        raise OSError("Автозапуск настраивается только в Windows.")
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        elif registered() is not None and is_enabled():
            winreg.DeleteValue(key, VALUE_NAME)
