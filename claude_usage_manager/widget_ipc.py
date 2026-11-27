"""Показ уже открытого виджета через событие текущей Windows-сессии."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys

SHOW_EVENT = "Local\\AILimitsMonitorShow"


def _allow_foreground(user):
    """Разрешить активацию по явному запросу пользователя без изменения Windows."""
    class KeyboardInput(ctypes.Structure):
        _fields_ = [("key", wintypes.WORD), ("scan", wintypes.WORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("extra", ctypes.c_size_t)]

    class MouseInput(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG),
                    ("data", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

    class InputData(ctypes.Union):
        _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("data", InputData)]

    # Windows снимает блокировку активации после ALT. Нажатие применяется
    # только при явном показе виджета, когда обычная активация не сработала.
    events = (Input * 2)()
    for event in events:
        event.type = 1
        event.data.keyboard.key = 0x12
    events[1].data.keyboard.flags = 2
    user.SendInput(2, events, ctypes.sizeof(Input))


def open_show_event():
    if sys.platform != "win32":
        return None
    kernel = ctypes.windll.kernel32
    kernel.CreateEventW.restype = ctypes.c_void_p
    handle = kernel.CreateEventW(None, False, False, SHOW_EVENT)
    if not handle:
        raise ctypes.WinError()
    return handle


def request_show():
    handle = open_show_event()
    if handle is not None:
        try:
            ctypes.windll.kernel32.SetEvent(ctypes.c_void_p(handle))
        finally:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def show_requested(handle):
    return handle is not None and ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0


def bring_to_front(window):
    window.show_normal()
    if sys.platform != "win32":
        return
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = ctypes.c_void_p
    foreground = user.GetForegroundWindow()
    current = ctypes.windll.kernel32.GetCurrentThreadId()
    target = user.GetWindowThreadProcessId(ctypes.c_void_p(foreground), None) if foreground else 0
    attached = bool(target and target != current and user.AttachThreadInput(current, target, True))
    try:
        hwnd = ctypes.c_void_p(int(window.winId()))
        user.ShowWindow(hwnd, 9)
        user.BringWindowToTop(hwnd)
        user.SetActiveWindow(hwnd)
        user.SetFocus(hwnd)
        user.SetForegroundWindow(hwnd)
        if user.GetForegroundWindow() != hwnd.value:
            _allow_foreground(user)
            user.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user.AttachThreadInput(current, target, False)
