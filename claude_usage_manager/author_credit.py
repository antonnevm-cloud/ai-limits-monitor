"""Ссылка на исходный проект в виджете и справке."""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import QPushButton


def github_link_button(object_name: str) -> QPushButton:
    button = QPushButton("GitHub")
    button.setObjectName(object_name)
    button.setAutoDefault(False)
    button.setFlat(True)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip("Открыть исходный проект на GitHub")
    button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
        "https://github.com/antonnevm-cloud/ai-limits-monitor"
    )))
    shortcut = QShortcut(button)
    shortcut.setKeys([QKeySequence(Qt.Key.Key_Return), QKeySequence(Qt.Key.Key_Enter)])
    shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
    shortcut.setAutoRepeat(False)
    shortcut.activated.connect(button.click)
    return button
