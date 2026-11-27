"""Offscreen tests for the small Claude bonus reset editor."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from claude_usage_manager.claude_reset_dialog import ClaudeManualResetsDialog


def app():
    return QApplication.instance() or QApplication([])


def test_compact_dialog_has_wrapped_explanation_and_no_raw_escape_sequences():
    qt = app()
    dialog = ClaudeManualResetsDialog()
    try:
        assert dialog.minimumWidth() >= 400
        assert dialog.maximumWidth() <= 640
        assert dialog.width() <= 640
        intro = dialog.findChild(QLabel, "ClaudeResetsIntro")
        warning = dialog.findChild(QLabel, "ClaudeResetsWarning")
        assert intro.wordWrap() is True
        assert warning.wordWrap() is True
        assert r"\n" not in intro.text()
        assert r"\n" not in warning.text()
        assert "ручной" in warning.text().lower()
        assert dialog.value_text() == ""
        assert dialog.count_label.text() == "Добавлено: 0"
    finally:
        dialog.close()
        qt.processEvents()


def test_add_edit_delete_and_existing_values_round_trip():
    qt = app()
    future = QDateTime.currentDateTime().addDays(4)
    timestamp = future.toString("yyyy-MM-dd HH:mm")
    dialog = ClaudeManualResetsDialog("-")
    try:
        assert dialog.offers.count() == 1
        dialog.expiry.setDateTime(future)
        dialog.add_dated_offer()
        assert dialog.value_text() == "-\n" + timestamp
        assert dialog.count_label.text() == "Добавлено: 2"
        dialog.offers.setCurrentRow(0)
        dialog.replace_selected_date()
        assert dialog.value_text() == timestamp + "\n" + timestamp
        dialog.offers.setCurrentRow(1)
        dialog.remove_selected()
        assert dialog.value_text() == timestamp
        assert dialog.count_label.text() == "Добавлено: 1"
    finally:
        dialog.close()
        qt.processEvents()


def test_expired_dates_and_more_than_thirty_offers_are_rejected(monkeypatch):
    qt = app()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    dialog = ClaudeManualResetsDialog()
    try:
        dialog.expiry.setDateTime(QDateTime.currentDateTime().addDays(-1))
        dialog.add_dated_offer()
        assert dialog.offers.count() == 0
        assert warnings
        for _ in range(30):
            dialog.add_without_expiry()
        assert dialog.offers.count() == 30
        assert dialog.add_button.isEnabled() is False
        assert dialog.add_without_expiry_button.isEnabled() is False
        dialog.add_without_expiry()
        assert dialog.offers.count() == 30
    finally:
        dialog.close()
        qt.processEvents()


def test_manual_dialog_no_long_qinputdialog_and_status_text_no_double_escaping():
    from pathlib import Path

    source = Path("claude_usage_manager/quota_monitor.py").read_text(
        encoding="utf-8"
    )
    assert "QInputDialog.getMultiLineText" not in source
    assert "ClaudeManualResetsDialog(" in source
    setup = source.split("    def configure_claude_statusline(self):", 1)[1].split(
        "    def show_claude_connection(self):", 1
    )[0]
    assert r"\\n" not in setup
