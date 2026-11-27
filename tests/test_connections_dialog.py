import json
import os
from itertools import combinations
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QProcess, QRect
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton
from claude_usage_manager import claude_local
from claude_usage_manager import connections_dialog as ui
from claude_usage_manager import provider_settings as settings


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("AI_LIMITS_DATA_DIR", str(tmp_path / "monitor"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    for variable in settings.PROFILE_VARIABLES.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(settings, "_binary_candidates", lambda key: iter(()))
    monkeypatch.setattr(settings, "antigravity_app", lambda connection=None: None)
    window = ui.ConnectionsDialog()
    yield window
    window.close()
    app.processEvents()


def _choose_program(page, tmp_path):
    path = tmp_path / settings.binary_name(page.provider)
    path.touch()
    path.chmod(0o755)
    page.inputs["executable"].setText(str(path))
    return path


def test_clean_dialog_offers_official_install_links(dialog, monkeypatch):
    opened = []
    monkeypatch.setattr(ui.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    assert dialog.tabs.count() == 4
    for key, page in dialog.pages.items():
        assert not page.install.isHidden()
        assert not page.login.isEnabled()
        assert "не найдена" in page.status.text()
        page.install.click()
        assert opened[-1] == settings.INSTALL_URLS[key]
    assert not (settings.utils.data_dir() / "connections.json").exists()


def test_all_tabs_in_monitor_theme_are_opaque_and_controls_fit(dialog, tmp_path, monkeypatch):
    from claude_usage_manager.qt_theme import COLORS, build_stylesheet
    from claude_usage_manager.quota_monitor import STYLE

    app = QApplication.instance()
    previous_style = app.styleSheet()
    app.setStyleSheet(build_stylesheet() + STYLE)
    try:
        long_path = tmp_path / ("long part of the profile path " * 6) / "data"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(long_path))
        for key, page in dialog.pages.items():
            if key != "claude":
                page.inputs["profile"].setText(str(long_path))
            page.refresh()
        dialog.show()
        for index, page in enumerate(dialog.pages.values()):
            dialog.tabs.setCurrentIndex(index)
            app.processEvents()
            pixmap = dialog.grab()
            image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            assert min(image.constBits()[3::4]) == 255
            scale = pixmap.devicePixelRatio()

            def color(widget, point):
                origin = widget.mapTo(dialog, point)
                return image.pixelColor(round(origin.x() * scale), round(origin.y() * scale)).name()

            assert color(dialog, QPoint(2, 2)).lower() == COLORS["bg"].lower()
            assert color(page, QPoint(3, 3)).lower() == COLORS["panel"].lower()
            bar = dialog.tabs.tabBar()
            for tab in range(bar.count()):
                inside = bar.tabRect(tab).topLeft() + QPoint(8, 6)
                expected = COLORS["selection"] if tab == index else COLORS["sidebar"]
                assert color(bar, inside).lower() == expected.lower()
            assert page.inputs["profile"].toolTip() == str(long_path)
            items = [*dialog.findChildren(QPushButton), *page.findChildren(QLineEdit)]
            rects = [QRect(item.mapTo(dialog, QPoint()), item.size()) for item in items if item.isVisible()]
            assert all(dialog.rect().contains(rect) for rect in rects)
            assert not any(a.intersects(b) for a, b in combinations(rects, 2))
    finally:
        app.setStyleSheet(previous_style)


def test_manual_statusline_uses_unsaved_profile_and_preserves_existing_handler(dialog, tmp_path):
    page = dialog.pages["claude"]
    _choose_program(page, tmp_path)
    first = tmp_path / "first"
    first.mkdir()
    existing = first / "settings.json"
    body = '{"statusLine":{"type":"command","command":"my-handler"}}'
    existing.write_text(body, encoding="utf-8")
    page.inputs["profile"].setText(str(first))
    page.statusline.click()
    assert "другая настройка" in page.result.text()
    assert existing.read_text(encoding="utf-8") == body
    second = tmp_path / "second"
    page.inputs["profile"].setText(str(second))
    page.statusline.click()
    saved = json.loads((second / "settings.json").read_text(encoding="utf-8"))
    assert saved["statusLine"]["command"] == claude_local.bridge_command(profile=second)
    assert "подключена" in page.result.text()
    assert settings.provider_profile("claude") != second
    page.inputs["profile"].setText("relative-folder")
    assert not page.statusline.isEnabled()
    page.statusline.click()
    assert not (settings.provider_profile("claude") / "settings.json").exists()


def test_cancel_discards_changes_and_save_round_trips(dialog, tmp_path):
    page = dialog.pages["codex"]
    page.inputs["profile"].setText(str(tmp_path / "unused"))
    dialog.reject()
    assert settings.load_connections()["codex"] == settings.Connection()
    page.reset.click()
    assert page.inputs["profile"].text() == ""
    manual = tmp_path / "new"
    page.inputs["profile"].setText(str(manual))
    page.enabled.setChecked(False)
    dialog.accept()
    assert settings.load_connections()["codex"] == settings.Connection(enabled=False, profile=str(manual))


@pytest.mark.parametrize("provider", ("claude", "codex", "grok", "antigravity"))
def test_login_passes_fixed_arguments_and_unsaved_profile(dialog, tmp_path, monkeypatch, provider):
    page = dialog.pages[provider]
    program = _choose_program(page, tmp_path)
    profile = tmp_path / "profile with space & literal $x"
    page.inputs["profile"].setText(str(profile))
    calls = []
    monkeypatch.setattr(settings.sys, "platform", "win32" if os.name == "nt" else "linux")
    monkeypatch.setattr(ui.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)))
    page.login.click()
    args, kwargs = calls[0]
    assert args == [str(program), *ui._LOGIN_ARGS[provider]]
    assert "shell" not in kwargs and "stdin" not in kwargs
    if provider in settings.PROFILE_VARIABLES:
        assert kwargs["env"][settings.PROFILE_VARIABLES[provider]] == str(profile)
    assert not (settings.utils.data_dir() / "connections.json").exists()


def test_check_discards_all_client_output_and_uses_exit_status(dialog, tmp_path, monkeypatch):
    page = dialog.pages["codex"]
    program = _choose_program(page, tmp_path)
    called = {}
    for name in ("setStandardInputFile", "setStandardOutputFile", "setStandardErrorFile"):
        monkeypatch.setattr(QProcess, name, lambda self, value, key=name: called.update({key: value}))
    monkeypatch.setattr(QProcess, "start", lambda self: None)
    page.check.click()
    process = page.pending
    assert process.program() == str(program)
    assert process.arguments() == ["login", "status"]
    assert len(called) == 3 and set(called.values()) == {os.devnull}
    process.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert page.result.text() == "Клиент подтвердил вход."


def test_antigravity_statusline_receives_both_unsaved_roots(dialog, tmp_path, monkeypatch):
    from claude_usage_manager import antigravity_quota

    called = {}
    monkeypatch.setattr(antigravity_quota, "install_antigravity_statusline",
                        lambda **kwargs: called.update(kwargs) or "installed")
    page = dialog.pages["antigravity"]
    _choose_program(page, tmp_path)
    history, cli = tmp_path / "history", tmp_path / "cli"
    page.inputs["profile"].setText(str(history))
    page.inputs["cli_profile"].setText(str(cli))
    page.statusline.click()
    assert called == {"profile": history, "cli_profile": cli}


def test_mac_terminal_launcher_keeps_paths_out_of_commands(dialog, tmp_path, monkeypatch):
    paths = {}
    program = tmp_path / "space $(text) & other" / "codex"
    profile = tmp_path / "custom 'profile'"

    def record_link(path, target, **kwargs):
        paths[path.name] = target
        path.touch()

    def failed_open(option, app, launch_file):
        assert option == "-a" and app.endswith("Terminal.app")
        launcher = Path(launch_file)
        assert launcher.name == "codex.command"
        script = launcher.read_text(encoding="utf-8")
        assert str(program) not in script and str(profile) not in script
        assert 'exec "$program" "$@"' in script
        raise OSError("simulated launch failure")

    monkeypatch.setattr(Path, "symlink_to", record_link)
    monkeypatch.setattr(ui, "_open_on_mac", failed_open)
    with pytest.raises(OSError, match="simulated"):
        ui._open_mac_terminal("codex", program, profile)
    assert paths == {"program": program, "profile": profile}
    assert not list(settings.utils.data_dir().glob("login-*"))
