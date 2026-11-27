import ctypes
import sys

import pytest

from claude_usage_manager import widget_ipc
from claude_usage_manager.utils import APP_DIR_NAME


def test_default_data_directory_keeps_existing_window_names(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    monkeypatch.setattr(widget_ipc, "_windows_local_appdata", lambda: local)
    monkeypatch.setenv("AI_LIMITS_DATA_DIR", str(local / APP_DIR_NAME))
    for name in (widget_ipc.WIDGET_MUTEX, widget_ipc.SHOW_EVENT):
        assert widget_ipc._object_name(name) == name


@pytest.mark.skipif(sys.platform != "win32", reason="Рабочие столы Windows")
def test_window_on_an_inactive_desktop_is_shown_without_taking_input(monkeypatch):
    shown = []

    class Window:
        def show(self):
            shown.append(True)

    monkeypatch.setattr(widget_ipc, "_receives_desktop_input", lambda _user: False)
    widget_ipc.bring_to_front(Window())
    assert shown == [True]


@pytest.mark.skipif(sys.platform != "win32", reason="Именованные объекты Windows")
def test_second_launch_only_activates_the_matching_profile(tmp_path, monkeypatch):
    roots = [tmp_path / "Профиль один", tmp_path / "Профиль два"]
    handles = []
    events = []
    try:
        for root in roots:
            monkeypatch.setenv("AI_LIMITS_DATA_DIR", str(root))
            mutex = widget_ipc.acquire_widget_mutex()
            assert mutex is not None
            handles.append(mutex)
            event = widget_ipc.open_show_event()
            events.append(event)
            handles.append(event)
        for i, root in enumerate(roots):
            monkeypatch.setenv("AI_LIMITS_DATA_DIR", str(root))
            assert widget_ipc.acquire_widget_mutex() is None
            widget_ipc.request_show()
            assert widget_ipc.show_requested(events[i])
            assert not widget_ipc.show_requested(events[1 - i])
    finally:
        for handle in handles:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
