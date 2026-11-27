"""Окно настройки источников. Вход всегда выполняет официальный клиент."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from . import provider_settings as settings
from . import utils
from .provider_settings import Connection
from .qt_theme import COLORS


_LOGIN_ARGS = {
    "claude": ("auth", "login"), "codex": ("login",),
    "grok": ("login", "--oauth"), "antigravity": (),
}
_CHECK_ARGS = {
    "claude": ("auth", "status"), "codex": ("login", "status"),
    "grok": ("usage", "--json"), "antigravity": ("--version",),
}

_DIALOG_STYLE = f"""
QDialog#ConnectionsDialog {{ background-color: {COLORS['bg']}; }}
QTabWidget#ConnectionTabs::pane {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
}}
QWidget[connectionPage="true"] {{ background-color: {COLORS['panel']}; }}
QTabBar#ConnectionTabBar::tab {{
    background: {COLORS['sidebar']}; color: {COLORS['muted']};
    border: 1px solid {COLORS['border_soft']};
    border-top-left-radius: 5px; border-top-right-radius: 5px;
    padding: 7px 12px; margin-right: 2px;
}}
QTabBar#ConnectionTabBar::tab:hover {{ background: {COLORS['selection_hover']}; }}
QTabBar#ConnectionTabBar::tab:selected {{
    background: {COLORS['selection']}; color: {COLORS['text']};
    border-bottom: 2px solid {COLORS['accent_hover']};
}}
"""

# Terminal.app открывает файлы .command. В скрипт никогда не подставляются пути
# из настроек: программа и профиль передаются отдельными символическими ссылками.
_MAC_LOGIN = """#!/bin/sh
dir=${0%/*}
program=$(/usr/bin/readlink "$dir/program") || exit 1
profile=$(/usr/bin/readlink "$dir/profile") || exit 1
case "${0##*/}" in
    claude.command) export CLAUDE_CONFIG_DIR="$profile"; set -- auth login ;;
    codex.command) export CODEX_HOME="$profile"; set -- login ;;
    grok.command) export GROK_HOME="$profile"; set -- login --oauth ;;
    antigravity.command) set -- ;;
    *) exit 1 ;;
esac
/bin/rm "$dir/program" "$dir/profile" "$0"
cd "${dir%/*}" || exit 1
/bin/rmdir "$dir"
exec "$program" "$@"
"""


def _open_on_mac(*args: str) -> None:
    subprocess.run(
        ["/usr/bin/open", *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=True, timeout=5,
    )


def _open_mac_terminal(provider: str, program: Path, profile: Path) -> None:
    # Отдельный каталог с правами 0700 на каждый вход, в том числе для двух окон.
    directory = Path(tempfile.mkdtemp(prefix="login-", dir=utils.data_dir()))
    launcher = directory / (provider + ".command")
    executable_link = directory / "program"
    profile_link = directory / "profile"
    try:
        executable_link.symlink_to(program)
        profile_link.symlink_to(profile, target_is_directory=True)
        launcher.write_text(_MAC_LOGIN, encoding="utf-8", newline="\n")
        launcher.chmod(0o700)
        _open_on_mac("-a", "/System/Applications/Utilities/Terminal.app", str(launcher))
    except (OSError, subprocess.SubprocessError):
        for path in (launcher, executable_link, profile_link):
            path.unlink(missing_ok=True)
        directory.rmdir()
        raise


def launch_login(provider: str, connection: Connection) -> None:
    settings.validate_connection(provider, connection)
    if not connection.enabled:
        raise ValueError("Подключение выключено.")
    executable = settings.provider_executable(provider, connection)
    app = settings.antigravity_app(connection) if provider == "antigravity" else None
    env = settings.provider_environment(provider, connection)
    if app and (connection.application or not connection.executable or executable is None):
        if sys.platform == "darwin":
            _open_on_mac(str(app))
        else:
            subprocess.Popen([str(app)], cwd=utils.data_dir(), env=env)
        return
    if executable is None:
        raise FileNotFoundError("Программа не найдена.")
    if sys.platform == "darwin":
        _open_mac_terminal(provider, executable, settings.provider_profile(provider, connection))
    else:
        flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        subprocess.Popen(
            [str(executable), *_LOGIN_ARGS[provider]], cwd=utils.data_dir(), env=env, creationflags=flags,
        )


class _ProviderPage(QWidget):
    def __init__(self, provider: str, existing: Connection, parent: QWidget) -> None:
        super().__init__(parent)
        self.provider = provider
        self.pending: QProcess | None = None
        self.inputs: dict[str, QLineEdit] = {}
        self.setObjectName(provider + "ConnectionPage")
        self.setProperty("connectionPage", True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 12)
        layout.setSpacing(12)
        self.enabled = QCheckBox("Включить источник")
        self.enabled.setChecked(existing.enabled)
        layout.addWidget(self.enabled)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(8)
        layout.addLayout(form)
        path_labels = {
            "executable": "Программа agy" if provider == "antigravity" else "Программа",
            "profile": "Каталог истории" if provider == "antigravity" else "Каталог данных",
        }
        if provider == "antigravity":
            path_labels["application"] = "Приложение для входа"
            path_labels["cli_profile"] = "Каталог настроек agy"
        for field, label in path_labels.items():
            row = QHBoxLayout()
            edit = QLineEdit(getattr(existing, field))
            self.inputs[field] = edit
            edit.setObjectName(provider + "_" + field)
            edit.setClearButtonEnabled(True)
            row.addWidget(edit, 1)
            button = QPushButton("Выбрать")
            button.clicked.connect(lambda checked=False, key=field: self.choose_path(key))
            row.addWidget(button)
            form.addRow(label, row)

        self.reset = QPushButton("Сбросить пути на авто")
        self.reset.clicked.connect(self.reset_paths)
        layout.addWidget(self.reset, 0)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.login = QPushButton("Войти")
        self.login.clicked.connect(self.sign_in)
        actions.addWidget(self.login)
        self.install = QPushButton("Страница установки")
        self.install.clicked.connect(self.open_install_page)
        actions.addWidget(self.install)
        self.check = QPushButton("Проверить")
        self.check.clicked.connect(self.check_connection)
        actions.addWidget(self.check)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.statusline: QPushButton | None = None
        if provider in {"claude", "antigravity"}:
            self.statusline = QPushButton("Подключить строку состояния")
            self.statusline.setToolTip("Сразу добавить источник лимитов в настройки выбранного профиля.")
            self.statusline.clicked.connect(self.connect_statusline)
            layout.addWidget(self.statusline)
        self.result = QLabel()
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        layout.addStretch(1)
        self.enabled.toggled.connect(self.refresh)
        for item in self.inputs.values():
            item.textChanged.connect(self.refresh)
        self.refresh()

    def selection(self) -> Connection:
        return Connection(
            enabled=self.enabled.isChecked(),
            **{key: item.text() for key, item in self.inputs.items()},
        )

    def choose_path(self, field: str) -> None:
        edit = self.inputs[field]
        start = edit.text() or str(Path.home())
        if field in {"profile", "cli_profile"}:
            path = QFileDialog.getExistingDirectory(self, "Выбери каталог", start)
        else:
            file_filter = "Программы (*.exe)" if sys.platform == "win32" else "Программы (*)"
            path, _ = QFileDialog.getOpenFileName(self, "Выбери официальную программу", start, file_filter)
        if path:
            edit.setText(path)

    def reset_paths(self) -> None:
        for edit in self.inputs.values():
            edit.blockSignals(True)
            edit.clear()
            edit.blockSignals(False)
        self.refresh()

    def refresh(self) -> None:
        self.stop_check()
        current = self.selection()
        self.result.clear()
        self.reset.setEnabled(any(edit.text() for edit in self.inputs.values()))
        try:
            settings.validate_connection(self.provider, current)
        except ValueError as error:
            for edit in self.inputs.values():
                edit.setToolTip(edit.text() or edit.placeholderText())
            self.status.setText(str(error))
            self.login.setEnabled(False)
            self.check.setEnabled(False)
            self.install.hide()
            if self.statusline is not None:
                self.statusline.setEnabled(False)
            return
        executable = settings.provider_executable(self.provider, current)
        profile = settings.provider_profile(self.provider, current)
        self.inputs["executable"].setPlaceholderText(str(executable) if executable else "Автоматически (не найдена)")
        self.inputs["profile"].setPlaceholderText(str(profile))
        descriptions = ["Программа найдена" if executable else "Программа не найдена"]
        descriptions.append("каталог данных найден" if profile.is_dir() else "каталог данных не найден")
        app = None
        if self.provider == "antigravity":
            app = settings.antigravity_app(current)
            self.inputs["application"].setPlaceholderText(str(app) if app else "Автоматически (не найдено)")
            cli_profile = settings.antigravity_cli_profile(current)
            self.inputs["cli_profile"].setPlaceholderText(str(cli_profile))
            descriptions.append("приложение найдено" if app else "приложение не найдено")
        for edit in self.inputs.values():
            edit.setToolTip(edit.text() or edit.placeholderText())
        self.status.setText(" · ".join(descriptions) if current.enabled else "Подключение выключено")
        self.login.setEnabled(current.enabled and bool(executable or app))
        self.install.setVisible(executable is None or (self.provider == "antigravity" and app is None))
        self.check.setEnabled(current.enabled)
        if self.statusline is not None:
            self.statusline.setEnabled(current.enabled and executable is not None)

    def sign_in(self) -> None:
        try:
            launch_login(self.provider, self.selection())
        except (OSError, ValueError, subprocess.SubprocessError):
            self.result.setText("Открыть вход не удалось. Проверь путь к программе.")
        else:
            if self.provider == "antigravity" and (self.inputs["profile"].text() or self.inputs["cli_profile"].text()):
                self.result.setText(
                    "Открыт штатный вход Antigravity. Для квоты из выбранного каталога "
                    "подключи строку состояния и открой свой профиль agy."
                )
                return
            self.result.setText("Заверши вход в официальном клиенте, затем нажми \"Проверить\".")

    def open_install_page(self) -> None:
        if not QDesktopServices.openUrl(QUrl(settings.INSTALL_URLS[self.provider])):
            self.result.setText("Не удалось открыть страницу установки в браузере.")

    def stop_check(self) -> None:
        process, self.pending = self.pending, None
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(250)

    def check_connection(self) -> None:
        self.refresh()
        if not self.check.isEnabled():
            return
        current = self.selection()
        executable = settings.provider_executable(self.provider, current)
        if not current.enabled or executable is None:
            if self.provider == "antigravity" and current.enabled and settings.antigravity_app(current):
                self.result.setText("Приложение найдено. Проверь вход в Antigravity или установи agy.")
            return
        process = QProcess(self)
        process.setProgram(str(executable))
        process.setArguments(list(_CHECK_ARGS[self.provider]))
        process.setWorkingDirectory(str(utils.data_dir()))
        env = QProcessEnvironment()
        for key, value in settings.provider_environment(self.provider, current).items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        # Для проверки достаточно кода выхода. Клиенты могут печатать сведения учетной записи.
        process.setStandardInputFile(os.devnull)
        process.setStandardOutputFile(os.devnull)
        process.setStandardErrorFile(os.devnull)
        process.finished.connect(lambda code, state: self._checked(process, code, state))
        process.errorOccurred.connect(lambda error: self._check_error(process, error))
        timer = QTimer(process)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._check_timeout(process))
        self.pending = process
        self.check.setEnabled(False)
        self.result.setText("Проверяю через официальный клиент...")
        timer.start(15000)
        process.start()

    def _finish_check(self, process: QProcess, message: str) -> None:
        if self.pending is process:
            self.pending = None
            self.result.setText(message)
            self.check.setEnabled(self.enabled.isChecked())
        process.deleteLater()

    def _check_timeout(self, process: QProcess) -> None:
        if self.pending is process:
            self.stop_check()
            self.check.setEnabled(True)
            self.result.setText("Клиент не ответил за 15 секунд.")

    def _check_error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._finish_check(process, "Не удалось запустить выбранную программу.")

    def _checked(self, process: QProcess, code: int, state: QProcess.ExitStatus) -> None:
        if state != QProcess.ExitStatus.NormalExit:
            message = "Клиент завершился с ошибкой."
        elif self.provider == "grok":
            message = "Grok Build подтвердил доступ к лимиту." if code == 0 else "Grok Build не подтвердил доступ к лимиту."
        elif self.provider == "antigravity":
            message = "Программа agy отвечает. Вход проверь в Antigravity." if code == 0 else "Не удалось проверить версию agy."
        else:
            message = "Клиент подтвердил вход." if code == 0 else "Клиент не подтвердил вход."
        self._finish_check(process, message)

    def connect_statusline(self) -> None:
        if self.statusline is None or not self.statusline.isEnabled():
            return
        current = self.selection()
        profile = settings.provider_profile(self.provider, current)
        try:
            if self.provider == "claude":
                from .claude_local import bridge_command, install_statusline_command

                status = install_statusline_command(bridge_command(profile=profile), settings_path=profile / "settings.json")
            else:
                from .antigravity_quota import install_antigravity_statusline

                status = install_antigravity_statusline(profile=profile, cli_profile=settings.antigravity_cli_profile(current))
        except (OSError, ValueError):
            self.result.setText("Не удалось подключить строку состояния. Проверь путь и доступ к settings.json.")
            return
        messages = {
            "installed": "Строка состояния подключена. Открой новую сессию клиента.",
            "already": "Строка состояния уже подключена.",
            "unchanged": "Строка состояния уже подключена.",
            "conflict": "В профиле есть другая настройка строки состояния. Монитор сохранил ее.",
        }
        self.result.setText(messages.get(status, "Клиент не подтвердил подключение строки состояния."))


class ConnectionsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConnectionsDialog")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setWindowTitle("Подключения")
        self.resize(650, 425)
        layout = QVBoxLayout(self)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.message.hide()
        try:
            connections = settings.load_connections(strict=True)
        except ValueError as error:
            self.message.setText(str(error))
            self.message.show()
            connections = {key: Connection(enabled=False) for key in settings.PROVIDER_NAMES}
        self.tabs = QTabWidget()
        self.tabs.setObjectName("ConnectionTabs")
        self.tabs.tabBar().setObjectName("ConnectionTabBar")
        layout.addWidget(self.tabs, 1)
        self.pages = {}
        for key, name in settings.PROVIDER_NAMES.items():
            page = _ProviderPage(key, connections[key], self.tabs)
            self.pages[key] = page
            self.tabs.addTab(page, name)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        try:
            settings.save_connections({key: page.selection() for key, page in self.pages.items()})
        except ValueError as error:
            self.message.setText(str(error))
            self.message.show()
            return
        except OSError:
            self.message.setText("Не удалось сохранить файл подключений. Проверь доступ к каталогу данных монитора.")
            self.message.show()
            return
        super().accept()

    def done(self, result: int) -> None:
        for page in self.pages.values():
            page.stop_check()
        super().done(result)
