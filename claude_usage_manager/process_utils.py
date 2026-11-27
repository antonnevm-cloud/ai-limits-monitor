"""Запуск дочерних процессов без видимых консольных окон Windows."""
from __future__ import annotations

import os
import subprocess
from typing import Any, Sequence


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


def run_hidden(command: Sequence[str] | str, **kwargs: Any) -> subprocess.CompletedProcess:
    """subprocess.run с полностью скрытым окном на Windows."""
    options = hidden_process_options()
    if "creationflags" in kwargs:
        options["creationflags"] = int(options.get("creationflags", 0)) | int(kwargs["creationflags"])
    kwargs.update(options)
    return subprocess.run(command, **kwargs)