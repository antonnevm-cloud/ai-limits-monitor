"""Ссылка на сайт разработчика в виджете и справке."""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import QPushButton


def author_site_button(text: str, object_name: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    button.setAutoDefault(False)
    button.setFlat(True)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip("Открыть сайт разработчика в браузере")
    button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://remont1c.ru")))
    shortcut = QShortcut(button)
    shortcut.setKeys([QKeySequence(Qt.Key.Key_Return), QKeySequence(Qt.Key.Key_Enter)])
    shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
    shortcut.setAutoRepeat(False)
    shortcut.activated.connect(button.click)
    return button
