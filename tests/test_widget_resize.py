"""Проверка обеих ручек настоящего окна, включая попадание в него на Windows."""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("scale", ("1", "1.25", "1.5"))
def test_widget_resize_at_desktop_scale(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "PYTHONIOENCODING": "utf-8",
        "QT_QPA_PLATFORM": "windows" if sys.platform == "win32" else "offscreen",
        "QT_ENABLE_HIGHDPI_SCALING": "0",
        "QT_SCALE_FACTOR": scale,
        "AI_LIMITS_DATA_DIR": str(tmp_path / "data"),
    }
    def run_window(*args):
        result = subprocess.run(
            [sys.executable, __file__, *args], cwd=root, env=env, capture_output=True, encoding="utf-8", timeout=30
        )
        assert result.returncode == 0, result.stderr
        return int(result.stdout.strip())

    chosen = run_window()
    assert run_window(str(chosen)) == chosen


def _private_windows_desktop():
    # Назначаем потоку отдельный рабочий стол до создания QApplication.
    # Он закрывается с процессом, пользователь все время остается на своем.
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateDesktopW.argtypes = (
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p
    )
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.SetThreadDesktop.argtypes = (wintypes.HANDLE,)
    desktop = user32.CreateDesktopW(f"LimitsResize{os.getpid()}", None, None, 0, 0x01FF, None)
    if not desktop or not user32.SetThreadDesktop(desktop):
        raise ctypes.WinError(ctypes.get_last_error())
    return user32


def _check_windows_hits(user32, window):
    import ctypes
    from ctypes import wintypes

    user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    user32.WindowFromPoint.argtypes = (wintypes.POINT,)
    user32.WindowFromPoint.restype = wintypes.HWND
    handle = int(window.winId())
    rect = wintypes.RECT()
    assert user32.GetClientRect(handle, ctypes.byref(rect))
    # Скругления вверху и внизу полностью прозрачны без подложки ручек.
    for edge, grip in window.grips.items():
        for inset in range(round(grip.width() * window.devicePixelRatioF())):
            x = inset if edge == "left" else rect.right - 1 - inset
            for y in (0, rect.bottom // 2, rect.bottom - 1):
                point = wintypes.POINT(x, y)
                assert user32.ClientToScreen(handle, ctypes.byref(point))
                assert user32.WindowFromPoint(point) == handle, (edge, x, y)


def _check_grips(window):
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtWidgets import QAbstractButton

    image = window.grab().toImage()
    scale = image.devicePixelRatio()
    assert scale == float(os.environ["QT_SCALE_FACTOR"])
    buttons = [b for b in window.findChildren(QAbstractButton) if b.isVisible()]
    assert buttons
    for grip in window.grips.values():
        assert not grip.underMouse() and grip.width() >= 10
        assert grip.cursor().shape() == Qt.CursorShape.SizeHorCursor
        assert "ширину" in grip.toolTip()
        pos = grip.mapTo(window, grip.rect().center())
        marker = image.pixelColor(round(pos.x() * scale), round(pos.y() * scale))
        background = image.pixelColor(round(pos.x() * scale), round((pos.y() + 30) * scale))
        assert marker.lightness() > background.lightness() + 60
        for x in range(grip.width()):
            assert window.childAt(grip.x() + x, pos.y()) is grip
        for button in buttons:
            bounds = QRect(button.mapTo(window, QPoint()), button.size())
            assert not grip.geometry().intersects(bounds), button.text()


def _drag(window, edge, dx):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    grip = window.grips[edge]
    before = window.geometry()
    start = grip.mapTo(window, grip.rect().center())
    end = window.mapToGlobal(start) + QPoint(dx, 0)
    handle = window.windowHandle()
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert grip.dragging
    QTest.mouseMove(handle, window.mapFromGlobal(end))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, window.mapFromGlobal(end))
    assert not grip.dragging
    after = window.geometry()
    assert after.width() == before.width() + (dx if edge == "right" else -dx)
    if edge == "left":
        assert after.right() == before.right()
    else:
        assert after.left() == before.left()
    assert after.top() == before.top() and after.height() == before.height()
    assert window.db.get_setting("monitor_width") == str(after.width())


def _exercise_window(expected_width):
    user32 = _private_windows_desktop() if sys.platform == "win32" else None
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from claude_usage_manager import quota_monitor as monitor
    from claude_usage_manager.database import Database
    from claude_usage_manager.models import QuotaSnapshot
    from claude_usage_manager.qt_theme import build_stylesheet
    from claude_usage_manager.quota_monitor_model import ProviderState
    from claude_usage_manager.unified_monitor import UnifiedMonitorWindow
    from claude_usage_manager.windows_ui import enable_per_monitor_dpi_awareness

    enable_per_monitor_dpi_awareness()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(build_stylesheet() + monitor.STYLE)
    states = {p: ProviderState(p, QuotaSnapshot(provider=p), False) for p in monitor.PROVIDERS}
    with patch.object(monitor, "cached_states", return_value=states), patch.object(QSystemTrayIcon, "show"):
        db = Database()
        if expected_width is None:
            db.set_setting("monitor_opacity", "20")
        window = UnifiedMonitorWindow(db, auto_refresh=False)
        window.timer.stop()
        if expected_width is None:
            window.move(window.screen().availableGeometry().topLeft() + QPoint(80, 64))
        window.show()
        QTest.qWait(monitor.FIT_SETTLE_MS + 20)
        if expected_width is None:
            _check_grips(window)
            if user32:
                _check_windows_hits(user32, window)
            for edge, dx in (("left", -48), ("right", 64), ("left", 24), ("right", -32)):
                _drag(window, edge, dx)
            chosen = window.width()
            window._done(window.data)
            QTest.qWait(monitor.FIT_SETTLE_MS + 20)
            assert window.width() == chosen
        else:
            assert window.width() == window.user_width == expected_width
        actual_width = window.width()
        window.close()
        window.quit_monitor()
        db.close()
        return actual_width


if __name__ == "__main__":
    print(_exercise_window(int(sys.argv[1]) if len(sys.argv) > 1 else None))
