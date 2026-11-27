import datetime as dt

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from claude_usage_manager.cache_threads import CacheThread
from claude_usage_manager.cache_threads_qt import CacheThreadsBlock, LinkButton


def test_list_action_uses_only_its_visible_button_and_accepts_keyboard():
    app = QApplication.instance() or QApplication([])
    area = QWidget()
    layout = QVBoxLayout(area)
    label = QLabel("Диалоги")
    button = LinkButton("Показать еще 2")
    layout.addWidget(label)
    layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
    area.resize(360, 100)
    area.show()
    app.processEvents()
    actions = QSignalSpy(button.clicked)
    assert QAccessible.queryAccessibleInterface(button).role() == QAccessible.Role.Button
    assert button.accessibleName() == button.text()
    assert button.width() < area.width()

    QTest.mouseClick(label, Qt.MouseButton.LeftButton)
    QTest.mouseClick(button, Qt.MouseButton.RightButton)
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=QPoint(-4, -4))
    assert actions.count() == 0
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    QTest.keyClick(button, Qt.Key.Key_Return)
    assert actions.count() == 3
    area.close()


def test_show_more_keeps_keyboard_focus_on_the_reverse_action():
    app = QApplication.instance() or QApplication([])
    now = dt.datetime.now(dt.timezone.utc)
    rows = [CacheThread("claude", str(i), f"Диалог {i}", "Демо", "Desktop", now, 3600) for i in range(4)]
    block = CacheThreadsBlock()
    block.set_content(rows, now)
    block.more_clicked.connect(lambda: block.set_content(rows, now, show_all=True))
    block.show()
    block.activateWindow()
    app.processEvents()
    block.link.setFocus()
    QTest.keyClick(block.link, Qt.Key.Key_Space)
    app.processEvents()
    assert len(block.rows) == 4
    assert block.link.text() == "Свернуть список" and block.link.hasFocus()
    block.close()
