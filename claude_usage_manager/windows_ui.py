"""Windows DPI and native-window helpers used by the Qt application."""
from __future__ import annotations

import ctypes
import os
from typing import Any

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
_PROCESS_PER_MONITOR_DPI_AWARE = 2
_GA_ROOT = 2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2


def enable_per_monitor_dpi_awareness() -> str:
    if os.name != "nt":
        return "not-windows"
    user32 = ctypes.windll.user32
    try:
        if user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        result = ctypes.windll.shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE)
        if result in (0, 0x80070005):
            return "per-monitor-v1"
    except (AttributeError, OSError):
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system-aware"
    except (AttributeError, OSError):
        pass
    return "unchanged"


def top_level_hwnd(root: Any) -> int:
    hwnd = int(root.winId()) if hasattr(root, "winId") else int(root.winfo_id())
    if os.name != "nt":
        return hwnd
    top = int(ctypes.windll.user32.GetAncestor(hwnd, _GA_ROOT))
    return top or hwnd


def set_window_always_on_top(root: Any, enabled: bool) -> bool:
    if os.name != "nt":
        return False
    try:
        hwnd = top_level_hwnd(root)
        if not hwnd:
            return False
        insert_after = ctypes.c_void_p(_HWND_TOPMOST if enabled else _HWND_NOTOPMOST)
        flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW
        result = ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), insert_after, 0, 0, 0, 0, flags)
        return bool(result)
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        return False
