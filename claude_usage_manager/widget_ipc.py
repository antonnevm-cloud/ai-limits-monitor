"""Показ уже открытого виджета через событие текущей Windows-сессии."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys

from .utils import APP_DIR_NAME, data_dir, data_store_id

SHOW_EVENT = "Local\\AILimitsMonitorShow"
WIDGET_MUTEX = "AIUsageMonitor_SingleInstance_v1"


def _windows_local_appdata() -> Path | None:
    # Папка зарегистрированного профиля Windows, включая перенаправленные папки.
    folder = ctypes.create_unicode_buffer(260)
    csidl_local_appdata = 0x001C
    result = ctypes.windll.shell32.SHGetFolderPathW(None, csidl_local_appdata, None, 0, folder)
    return Path(folder.value) if result == 0 else None


def _object_name(base: str) -> str:
    local_appdata = _windows_local_appdata()
    if local_appdata and data_dir().resolve() == (local_appdata / APP_DIR_NAME).resolve():
        # Прежние средства запуска умеют показать окно стандартного профиля по этому имени.
        return base
    return f"{base}-{data_store_id()}"


def acquire_widget_mutex():
    if sys.platform != "win32":
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    name = _object_name(WIDGET_MUTEX)
    ctypes.set_last_error(0)
    mutex = kernel.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if not mutex:
        raise ctypes.WinError(error)
    if error == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(wintypes.HANDLE(mutex))
        return None
    return mutex


ASFW_ANY = 0xFFFFFFFF


def open_show_event():
    if sys.platform != "win32":
        return None
    kernel = ctypes.windll.kernel32
    kernel.CreateEventW.restype = ctypes.c_void_p
    handle = kernel.CreateEventW(None, False, False, _object_name(SHOW_EVENT))
    if not handle:
        raise ctypes.WinError()
    return handle


def request_show():
    handle = open_show_event()
    if handle is not None:
        try:
            # Повторный запуск сделал пользователь: право вывести окно на передний план
            # передается уже открытому экземпляру, без перехвата ввода чужих окон.
            ctypes.windll.user32.AllowSetForegroundWindow(wintypes.DWORD(ASFW_ANY))
            ctypes.windll.kernel32.SetEvent(ctypes.c_void_p(handle))
        finally:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def show_requested(handle):
    return handle is not None and ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0


def _receives_desktop_input(user):
    user.GetThreadDesktop.restype = wintypes.HANDLE
    desktop = user.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
    receiving = wintypes.BOOL()
    uoi_io = 6
    return bool(desktop and user.GetUserObjectInformationW(
        wintypes.HANDLE(desktop), uoi_io, ctypes.byref(receiving), ctypes.sizeof(receiving), None
    ) and receiving.value)


def bring_to_front(window):
    if sys.platform != "win32":
        window.show_normal()
        return
    user = ctypes.windll.user32
    if not _receives_desktop_input(user):
        window.show()
        return
    window.show_normal()
    # Право на активацию дает запуск пользователем (первый запуск) или AllowSetForegroundWindow
    # повторного запуска. Без него Windows оставляет фокус у текущей программы, а окно
    # поверх остальных все равно видно: ввод чужих окон не перехватывается и не подменяется.
    hwnd = ctypes.c_void_p(int(window.winId()))
    user.ShowWindow(hwnd, 9)  # SW_RESTORE
    user.SetForegroundWindow(hwnd)
