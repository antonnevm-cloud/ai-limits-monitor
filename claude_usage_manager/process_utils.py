"""Запуск дочерних процессов без видимых консольных окон Windows."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

# После снятия дерева процессов ответ ждем недолго: каналы мог держать внук, которого уже нет.
_AFTER_KILL_WAIT = 5


def hidden_process_options() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def kill_process_tree(process: subprocess.Popen) -> None:
    """Завершить процесс вместе с дочерними: иначе внук держит каналы вывода и срок не работает."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            subprocess.run([str(taskkill), "/T", "/F", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=False, **hidden_process_options())
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def run_hidden(command: Sequence[str] | str, **kwargs: Any) -> subprocess.CompletedProcess:
    """subprocess.run с полностью скрытым окном на Windows и жестким сроком ожидания.

    По сроку снимается все дерево процессов. Если каналы все равно не закрылись,
    TimeoutExpired выдается без бесконечного ожидания.
    """
    options = hidden_process_options()
    if "creationflags" in kwargs:
        options["creationflags"] = int(options.get("creationflags", 0)) | int(kwargs["creationflags"])
    kwargs.update(options)
    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)
    data = kwargs.pop("input", None)
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if data is not None:
        kwargs["stdin"] = subprocess.PIPE
    process = subprocess.Popen(command, **kwargs)
    try:
        stdout, stderr = process.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=_AFTER_KILL_WAIT)
        except subprocess.TimeoutExpired:
            stdout = stderr = None
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from None
    except BaseException:
        kill_process_tree(process)
        raise
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


def reset_inherited_dll_directory() -> None:
    """Собранная программа Windows не передает свой каталог DLL клиентам, которые запускает.

    Загрузчик PyInstaller задает SetDllDirectory на каталог _internal, и дочерние процессы
    (codex, grok, agy, claude) брали бы оттуда VCRUNTIME140, sqlite3 и libcrypto вместо своих.
    Сама программа продолжает находить свои DLL через AddDllDirectory.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    bundle = getattr(sys, "_MEIPASS", None)
    if not bundle:
        return
    import ctypes

    global _BUNDLE_DLL_DIRECTORY
    try:
        _BUNDLE_DLL_DIRECTORY = os.add_dll_directory(bundle)
    except OSError:
        return
    ctypes.windll.kernel32.SetDllDirectoryW(None)


_BUNDLE_DLL_DIRECTORY = None
