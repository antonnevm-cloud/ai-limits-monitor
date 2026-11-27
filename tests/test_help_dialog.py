import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QWidget

from claude_usage_manager.help_dialog import HelpDialog
from claude_usage_manager.provider_settings import PROVIDER_NAMES


def test_help_works_without_user_data(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    data = tmp_path / "monitor"
    monkeypatch.setenv("AI_LIMITS_DATA_DIR", str(data))
    parent = QWidget()
    dialog = HelpDialog(parent)
    dialog.show()
    app.processEvents()

    assert dialog.windowTitle() == "Справка"
    assert dialog.parentWidget() is parent
    assert not dialog.isModal()
    assert parent.isEnabled()
    pages = {}
    for index in range(dialog.tabs.count()):
        dialog.tabs.setCurrentIndex(index)
        page = dialog.tabs.currentWidget()
        pages[dialog.tabs.tabText(index)] = page.toPlainText()
        assert page.isVisible() and page.isReadOnly()
        assert page.accessibleName() and not page.openLinks() and not page.openExternalLinks()
    assert all(name in pages["Источники"] for name in PROVIDER_NAMES.values())
    assert "Сбросить пути на авто" in pages["Нет данных"]
    assert "процентных пунктах" in pages["История"]
    assert not data.exists()

    close = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close)
    assert close.text() == "Закрыть"
    close.click()
    assert not dialog.isVisible()


def test_help_sections_and_scroll_work_from_keyboard():
    app = QApplication.instance() or QApplication([])
    dialog = HelpDialog()
    dialog.resize(460, 245)
    dialog.show()
    app.processEvents()

    bar = dialog.tabs.tabBar()
    bar.setFocus()
    QTest.keyClick(bar, Qt.Key.Key_Right)
    assert dialog.tabs.currentIndex() == 1
    page = dialog.tabs.currentWidget()
    page.setFocus()
    app.processEvents()
    assert page.horizontalScrollBar().maximum() == 0
    assert page.verticalScrollBar().maximum() > 0
    QTest.keyClick(page, Qt.Key.Key_PageDown)
    assert page.verticalScrollBar().value() > 0
    QTest.keyClick(page, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    dialog.showNormal()
    assert dialog.isVisible()
