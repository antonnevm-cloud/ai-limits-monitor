import datetime as dt
import json
import os
from threading import Event
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QToolButton, QWidget

from claude_usage_manager.cache_threads import CacheThread, ThreadScanner
from claude_usage_manager.cache_threads_qt import (
    WARN_SECONDS,
    ACTIVITY_COLORS,
    CACHE_BLOCK_MIN_WIDTH,
    COMPACT_RING_SIZE,
    COMPACT_ROW_HEIGHT,
    EMPTY_TEXT,
    ROW_HEIGHT,
    SPINNER_PERIOD,
    CacheRing,
    CacheThreadRow,
    CacheThreadsBlock,
    SpinnerClock,
    StateDot,
    distribute_height_caps,
    spinner_angle,
)
from claude_usage_manager.database import Database
from claude_usage_manager.edge_grip import EdgeGrip
from claude_usage_manager.quota_monitor import CompactRow, MonitorWindow
from claude_usage_manager.thread_activity import ACTIVITY_NEEDS_REPLY, ACTIVITY_UNREAD, ACTIVITY_WORKING
from claude_usage_manager.thread_view import ThreadView
from claude_usage_manager.toggle_switch import ToggleSwitch


def qapp():
    return QApplication.instance() or QApplication([])


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def thread(session, started_ago, ttl=3600, agent="claude", estimated=False, title=None):
    return CacheThread(
        agent=agent, session_id=session, title=title or f"Тред {session}", project="demo_shop",
        environment="Desktop", started_at=utcnow() - dt.timedelta(seconds=started_ago),
        ttl_seconds=ttl, estimated=estimated,
    )


def many(count, agent="claude"):
    return [thread(f"t{i}", 100 + i * 10, agent=agent) for i in range(count)]


def window(tmp_path, name="cache.db"):
    app = qapp()
    db = Database(tmp_path / name)
    w = MonitorWindow(db, auto_refresh=False)
    w.timer.stop()
    app.processEvents()
    return app, db, w


def close(app, db, w):
    w.quit_monitor()
    app.processEvents()
    db.close()


def provider_row(w, provider):
    for widget in w.findChildren(CompactRow):
        if widget.row.provider == provider and not widget.isHidden() and widget.parent() is not None:
            return widget
    raise AssertionError(f"нет строки {provider}")


def center_in_window(w, widget):
    return widget.mapTo(w, widget.rect().center())


# ------------------------------- Окошко ------------------------------------ #
def test_ring_color_goes_from_green_to_red_and_cooled_text():
    qapp()
    green = CacheRing.fill_color(1.0)
    yellow = CacheRing.fill_color(0.3)
    red = CacheRing.fill_color(0.02)
    assert round(green.hslHueF() * 360) == 130
    assert 40 < round(yellow.hslHueF() * 360) < 90
    assert round(red.hslHueF() * 360) < 15
    ring = CacheRing()
    ring.set_state(0.0, "остыл", True)
    assert ring.text == "остыл" and ring.fraction == 0.0


# ------------------------------- Список ------------------------------------ #
def test_block_shows_first_three_with_link_and_expands_and_collapses():
    app = qapp()
    block = CacheThreadsBlock()
    now = utcnow()
    threads = many(8)
    assert block.set_content(threads, now) is True
    assert len(block.rows) == 3 and block.link is not None and block.link.text() == "Показать еще 5"
    expanded = []
    block.more_clicked.connect(lambda: expanded.append(1))
    block.show()
    QTest.mouseClick(block.link, Qt.MouseButton.LeftButton)
    assert expanded == [1]
    assert block.set_content(threads, now, show_all=True) is True
    assert len(block.rows) == 8 and block.link.text() == "Свернуть список"
    collapsed = []
    block.less_clicked.connect(lambda: collapsed.append(1))
    QTest.mouseClick(block.link, Qt.MouseButton.LeftButton)
    assert collapsed == [1]
    assert block.set_content(threads, now, show_all=False) is True and len(block.rows) == 3
    block.deleteLater()
    app.processEvents()


def test_block_short_list_has_no_link_and_empty_list_shows_note():
    qapp()
    block = CacheThreadsBlock()
    now = utcnow()
    block.set_content(many(3), now, show_all=True)
    assert len(block.rows) == 3 and block.link is None
    block.set_content([], now)
    assert block.rows == [] and block.note is not None and block.note.text() == EMPTY_TEXT
    block.set_content([], now, message="Нет данных")
    assert block.note.text() == "Нет данных"


def test_block_updates_rows_in_place_when_set_is_unchanged():
    qapp()
    block = CacheThreadsBlock()
    now = utcnow()
    assert block.set_content(many(2), now) is True
    same_rows = list(block.rows)
    before = block.rows[0].ring.text
    assert block.set_content([thread("t0", 700), thread("t1", 710)], now) is False
    assert block.rows == same_rows and block.rows[0].ring.text != before


def test_block_row_text_and_estimated_marker():
    qapp()
    block = CacheThreadsBlock()
    now = utcnow()
    block.set_content([thread("a", 100, ttl=300, agent="codex", estimated=True)], now)
    chip_text = block.rows[0].ring.text
    assert chip_text.startswith("~") and chip_text[1:].count(":") == 1
    assert block.rows[0].project._full == "demo_shop"  # компактно: среда в подсказке
    block.set_content([thread("a", 100, ttl=300, agent="codex", estimated=True)], now, view=ThreadView(compact=False))
    assert block.rows[0].project._full == "demo_shop · Desktop"


# ------------------------------- Окно -------------------------------------- #
def test_default_window_is_collapsed_like_before_even_with_threads(tmp_path):
    app, db, w = window(tmp_path)
    w.cache_threads = {"claude": many(3), "codex": many(2, agent="codex")}
    w._render()
    app.processEvents()
    assert w.cache_blocks == {}
    close(app, db, w)


def test_disclosure_expands_and_collapses_and_state_survives_restart(tmp_path):
    app, db, w = window(tmp_path)
    w.cache_threads = {"claude": many(3)}
    w.show()
    app.processEvents()
    button = provider_row(w, "claude").disclosure
    assert button.isVisible() and button.arrowType() == Qt.ArrowType.RightArrow
    assert "Развернуть" in button.accessibleName()
    pos = center_in_window(w, button)
    assert w.childAt(pos) is button
    QTest.mouseClick(w.windowHandle(), Qt.MouseButton.LeftButton, pos=pos)
    app.processEvents()
    assert button.isChecked() and button.arrowType() == Qt.ArrowType.DownArrow
    assert set(w.cache_blocks) == {"claude"} and w.cache_blocks["claude"].thread_keys == ["t0", "t1", "t2"]
    assert w.minimumWidth() >= CACHE_BLOCK_MIN_WIDTH
    assert json.loads(db.get_setting("monitor_cache_expanded", "{}")) == {"claude": True}
    close(app, db, w)

    db2 = Database(tmp_path / "cache.db")
    w2 = MonitorWindow(db2, auto_refresh=False)
    w2.timer.stop()
    assert w2.cache_expanded == {"claude": True}
    w2.cache_threads = {"claude": many(3)}
    w2._render()
    w2.show()
    app.processEvents()
    assert "claude" in w2.cache_blocks
    button = provider_row(w2, "claude").disclosure
    assert "Свернуть" in button.toolTip()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Return)
    app.processEvents()
    assert w2.cache_blocks == {} and json.loads(db2.get_setting("monitor_cache_expanded", "{}")) == {}
    assert not button.isChecked() and button.arrowType() == Qt.ArrowType.RightArrow
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert "claude" in w2.cache_blocks
    close(app, db2, w2)


def test_click_on_quota_or_name_keeps_card_state_and_drag_moves_the_window(tmp_path):
    app, db, w = window(tmp_path)
    w.show()
    app.processEvents()
    row = provider_row(w, "claude")
    for label in (row.name_label, row.dot, row.icon, row.quota_columns.headlines[0]):
        QTest.mouseClick(w.windowHandle(), Qt.MouseButton.LeftButton, pos=center_in_window(w, label))
        assert not w.cache_blocks
    old_pos = w.pos()
    pos = center_in_window(w, w.findChild(QLabel, 'WidgetTitle'))
    offset = QPoint(30, 8)
    QTest.mousePress(w.windowHandle(), Qt.MouseButton.LeftButton, pos=pos)
    QTest.mouseMove(w.windowHandle(), pos + offset)
    QTest.mouseRelease(w.windowHandle(), Qt.MouseButton.LeftButton, pos=pos)
    app.processEvents()
    assert w.pos() == old_pos + offset and not w.cache_blocks
    close(app, db, w)


def test_more_link_shows_all_threads_and_collapsing_row_resets_it(tmp_path):
    app, db, w = window(tmp_path)
    w.cache_threads = {"claude": many(8)}
    w.toggle_cache_provider("claude")
    block = w.cache_blocks["claude"]
    w.show()
    assert len(block.rows) == 3 and block.link.text() == "Показать еще 5"
    QTest.mouseClick(block.link, Qt.MouseButton.LeftButton)
    app.processEvents()
    block = w.cache_blocks["claude"]
    assert len(block.rows) == 8 and block.link.text() == "Свернуть список"
    QTest.keyClick(block.link, Qt.Key.Key_Space)
    app.processEvents()
    assert len(w.cache_blocks["claude"].rows) == 3
    QTest.mouseClick(w.cache_blocks["claude"].link, Qt.MouseButton.LeftButton)
    w.toggle_cache_provider("claude")
    w.toggle_cache_provider("claude")
    assert len(w.cache_blocks["claude"].rows) == 3
    close(app, db, w)


def test_every_agent_shows_its_threads_or_the_empty_note(tmp_path):
    app, db, w = window(tmp_path)
    for provider in ("codex", "grok"):
        w.toggle_cache_provider(provider)
        assert w.cache_blocks[provider].note.text() == EMPTY_TEXT
    w._cache_scanned({"grok": many(2, agent="grok")})
    app.processEvents()
    assert w.cache_blocks["grok"].thread_keys == ["t0", "t1"]
    assert w.cache_blocks["codex"].note.text() == EMPTY_TEXT
    close(app, db, w)


def test_antigravity_opens_quota_with_cache_timers_disabled(tmp_path):
    app, db, w = window(tmp_path)
    w.show()
    app.processEvents()
    row = provider_row(w, "antigravity")
    QTest.mouseClick(row.name_label, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert "antigravity" not in w.cache_blocks and not w.cache_expanded
    assert row.cursor().shape() != Qt.CursorShape.PointingHandCursor
    assert all(h.disclosure.cursor().shape() == Qt.CursorShape.PointingHandCursor for h in w.header_rows.values())
    assert len({h.name_label.mapTo(w, QPoint()).x() for h in w.header_rows.values()}) == 1
    assert len({h.disclosure.mapTo(w, QPoint()).x() for h in w.header_rows.values()}) == 1
    w.set_cache_enabled(False)
    button = row.disclosure
    assert button.isVisible() and button.accessibleName() == "Развернуть лимиты Antigravity"
    assert provider_row(w, "grok").disclosure.isHidden()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Right)
    assert button.isChecked() and not w.cache_blocks
    assert row.parentWidget().block.findChild(QLabel, "QuotaEmpty").text()
    QTest.keyClick(button, Qt.Key.Key_Left)
    assert not button.isChecked() and row.parentWidget().block is None
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert db.get_setting("monitor_ag_quota_expanded") == "1"
    close(app, db, w)
    app, db, reopened = window(tmp_path)
    assert provider_row(reopened, "antigravity").disclosure.isChecked()
    assert not reopened.cache_blocks
    close(app, db, reopened)


def test_stored_antigravity_expansion_is_ignored(tmp_path):
    qapp()
    db = Database(tmp_path / "old.db")
    db.set_setting("monitor_cache_expanded", json.dumps({"antigravity": True, "grok": True}))
    w = MonitorWindow(db, auto_refresh=False)
    w.timer.stop()
    assert w.cache_expanded == {"grok": True}
    close(QApplication.instance(), db, w)


def test_scanner_gets_ttl_estimates_from_settings(tmp_path):
    qapp()
    db = Database(tmp_path / "ttl.db")
    db.set_setting("monitor_cache_grok_ttl", "1800")
    db.set_setting("monitor_cache_codex_ttl", "777")
    w = MonitorWindow(db, auto_refresh=False)
    w.timer.stop()
    assert (w.cache_scanner._codex_ttl, w.cache_scanner._grok_ttl) == (3600, 1800)
    close(QApplication.instance(), db, w)


def test_tooltip_names_the_agent_and_the_basis_of_the_term():
    from claude_usage_manager.cache_threads_qt import tooltip_text
    exact = CacheThread("codex", "c1", "Тред", "proj", "Desktop", utcnow(), 1800, False, "Срок по документации OpenAI")
    assert "Codex" in tooltip_text(exact) and "Срок по документации OpenAI" in tooltip_text(exact)
    assert "оценка" not in tooltip_text(exact)
    estimate = CacheThread("grok", "g1", "Тред", "proj", "терминал", utcnow(), 900, True, "xAI срок кэша не публикует")
    assert "Grok Build" in tooltip_text(estimate) and "xAI срок кэша не публикует" in tooltip_text(estimate)
    plain = CacheThread("claude", "a1", "Тред", "proj", "Desktop", utcnow(), 3600)
    assert "Claude Code" in tooltip_text(plain) and "оценка" not in tooltip_text(plain)


def place_bottom_right(w):
    area = w.screen().availableGeometry()
    w.move(area.right() - w.width() - 20, area.bottom() - w.height() - 20)
    QApplication.instance().processEvents()
    return w.geometry()


def test_expanding_grows_up_and_left_keeping_bottom_right_corner(tmp_path):
    app, db, w = window(tmp_path)
    w._cache_scanned({"claude": many(5)})
    w.show()
    app.processEvents()
    w._fit_to_content()  # до показа окно берет запас под невидимую раскладку (HIDDEN_LAYOUT_RESERVE)
    before = place_bottom_right(w)
    w.toggle_cache_provider("claude")
    app.processEvents()
    after = w.geometry()
    assert after.height() > before.height()
    assert (after.right(), after.bottom()) == (before.right(), before.bottom())
    assert after.top() < before.top() and after.left() <= before.left()
    w.toggle_cache_provider("claude")
    app.processEvents()
    collapsed = w.geometry()
    assert collapsed.height() == before.height()
    assert (collapsed.right(), collapsed.bottom()) == (before.right(), before.bottom())
    close(app, db, w)


def test_growth_stops_at_screen_edge_instead_of_leaving_the_screen(tmp_path):
    app, db, w = window(tmp_path)
    w.cache_threads = {"claude": many(8)}
    w.show()
    app.processEvents()
    area = w.screen().availableGeometry()
    w.move(area.right() - w.width() + 1, area.top() + 10)
    app.processEvents()
    before = w.geometry()
    w.toggle_cache_provider("claude")
    app.processEvents()
    assert w.geometry().top() >= area.top() and w.geometry().left() >= area.left()
    assert w.geometry().right() == before.right()
    close(app, db, w)


def test_anchored_geometry_keeps_position_when_size_is_unchanged(tmp_path):
    app, db, w = window(tmp_path)
    current = w.geometry()
    assert w._anchored_geometry(current, current.width(), current.height()) == current
    close(app, db, w)


def test_scan_result_updates_expanded_blocks_and_ignores_collapsed(tmp_path):
    app, db, w = window(tmp_path)
    w.toggle_cache_provider("claude")
    assert w.cache_blocks["claude"].note is not None
    w._cache_scanned({"claude": many(2), "codex": many(1, agent="codex")})
    app.processEvents()
    assert w.cache_blocks["claude"].thread_keys == ["t0", "t1"] and "codex" not in w.cache_blocks
    block = w.cache_blocks["claude"]
    w._cache_scanned({"claude": [thread("t0", 900), thread("t1", 910)]})
    assert w.cache_blocks["claude"] is block
    close(app, db, w)


def test_ticking_updates_timers_without_rebuilding(tmp_path):
    app, db, w = window(tmp_path)
    w.cache_threads = {"claude": [thread("c1", 3000)]}
    w.toggle_cache_provider("claude")
    block = w.cache_blocks["claude"]
    before = block.rows[0].ring.text
    block.rows[0].thread = thread("c1", 3200)
    w._tick_cache()
    assert block.rows[0].ring.text != before and w.cache_blocks["claude"] is block
    close(app, db, w)


def test_toggle_in_tray_disables_blocks_and_clicks_and_saves_setting(tmp_path):
    app, db, w = window(tmp_path)
    w.cache_threads = {"claude": many(2)}
    w.toggle_cache_provider("claude")
    assert "claude" in w.cache_blocks
    w.set_cache_enabled(False)
    app.processEvents()
    assert w.cache_blocks == {} and db.get_setting("monitor_cache_threads", "1") == "0"
    assert w.cache_action.isChecked() is False
    w.show()
    app.processEvents()
    row = provider_row(w, "claude")
    assert row.disclosure.isHidden()
    QTest.mouseClick(w.windowHandle(), Qt.MouseButton.LeftButton, pos=center_in_window(w, row.name_label))
    assert w.cache_blocks == {}
    close(app, db, w)
    db2 = Database(tmp_path / "cache.db")
    w2 = MonitorWindow(db2, auto_refresh=False)
    assert w2.cache_enabled is False
    close(app, db2, w2)


def test_hidden_provider_has_no_block_even_if_expanded(tmp_path):
    app, db, w = window(tmp_path)
    w.cache_expanded["claude"] = True
    w.visibility["compact:claude"] = False
    w._render()
    assert "claude" not in w.cache_blocks
    close(app, db, w)


def test_background_scan_reads_local_logs_end_to_end(tmp_path):
    app, db, w = window(tmp_path)
    claude_dir = tmp_path / "claude"
    started = utcnow() - dt.timedelta(seconds=120)
    transcript = claude_dir / "projects" / "p" / "s.jsonl"
    transcript.parent.mkdir(parents=True)
    record = {
        "type": "assistant", "timestamp": started.isoformat().replace("+00:00", "Z"),
        "sessionId": "s-1", "cwd": str(tmp_path / "work" / "demo"), "entrypoint": "cli",
        "message": {"id": "m1", "usage": {
            "input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 900}}},
    }
    transcript.write_text(json.dumps(record) + "\n", encoding="utf-8")
    w.cache_scanner = ThreadScanner(claude_dir=claude_dir, codex_dir=tmp_path / "codex")
    w.cache_expanded["claude"] = True
    w._render()
    w._start_cache_scan()
    for _ in range(100):
        if w._cache_worker is None:
            break
        QTest.qWait(50)
    app.processEvents()
    assert w._cache_worker is None
    # тред Claude Code из терминала по умолчанию скрыт и не входит в счетчик работающих
    assert w.cache_blocks["claude"].rows == [] and [t.session_id for t in w.cache_threads["claude"]] == ["s"]
    w.toggle_drawer()
    QTest.mouseClick(w.view_switches["terminal"], Qt.MouseButton.LeftButton)
    app.processEvents()
    row = w.cache_blocks["claude"].rows[0]
    assert row.thread.project == "demo" and row.thread.environment == "терминал"
    assert 3400 < row.thread.remaining(utcnow()) < 3500
    close(app, db, w)


def test_claude_terminal_threads_hidden_by_switch_but_codex_and_grok_stay(tmp_path):
    app, db, w = window(tmp_path, "terminal.db")
    w.show()  # как в жизни: строки, убранные из окна, прячутся и отключают бегущие точки
    desktop = replace(thread("d", 100), activity=ACTIVITY_WORKING)
    terminal = replace(thread("t", 110), environment="терминал", activity=ACTIVITY_WORKING)
    scripted = replace(thread("s", 120), environment="sdk-cli")
    codex = replace(thread("c", 100, agent="codex"), environment="терминал")
    grok = replace(thread("g", 100, agent="grok"), environment="терминал")
    w.cache_threads = {"claude": [desktop, terminal, scripted], "codex": [codex], "grok": [grok]}
    w.cache_expanded = {"claude": True, "codex": True, "grok": True}
    w._render()
    app.processEvents()
    assert w.cache_blocks["claude"].thread_keys == ["d"] and w.running_count("claude") == 1
    assert w.cache_blocks["codex"].thread_keys == ["c"] and w.cache_blocks["grok"].thread_keys == ["g"]
    w.toggle_drawer()
    QTest.mouseClick(w.view_switches["terminal"], Qt.MouseButton.LeftButton)
    app.processEvents()
    assert w.cache_blocks["claude"].thread_keys == ["d", "t", "s"] and w.running_count("claude") == 2
    QTest.keyClick(w.view_switches["terminal"], Qt.Key.Key_Space)
    assert w.cache_blocks["claude"].thread_keys == ["d"]
    assert w.cache_blocks["codex"].thread_keys == ["c"] and w.cache_blocks["grok"].thread_keys == ["g"]
    for block in w.cache_blocks.values():  # бегущие точки отключить: общий таймер анимации проверяется в других тестах
        for row in block.rows:
            row.dot.set_state("")
    close(app, db, w)


def test_no_scan_when_nothing_is_expanded(tmp_path):
    app, db, w = window(tmp_path)
    w._start_cache_scan()
    assert w._cache_worker is None
    close(app, db, w)


def test_window_does_not_drift_across_restarts_with_an_expanded_list(tmp_path):
    """Регрессия ревью: при перезапуске с развернутым списком окно сползало на десятки пикселей за запуск."""
    app = qapp()
    db = Database(tmp_path / "restart.db")
    w = MonitorWindow(db, auto_refresh=False)
    w.timer.stop()
    w.show()
    app.processEvents()
    w.cache_threads = {"claude": many(5)}
    w.toggle_cache_provider("claude")
    app.processEvents()
    place_bottom_right(w)
    w.close()
    app.processEvents()
    tops = []
    for _ in range(3):
        db2 = db
        w2 = MonitorWindow(db2, auto_refresh=False)
        w2.timer.stop()
        w2.show()
        app.processEvents()
        w2._cache_scanned({"claude": many(5)})
        app.processEvents()
        tops.append((w2.geometry().top(), w2.geometry().bottom(), w2.geometry().right()))
        w2.close()
        app.processEvents()
        w2.quit_monitor()
        app.processEvents()
    assert len(set(tops)) == 1, tops
    db.close()


# --------------------- Высота окна и точка состояния ----------------------- #
def test_distribute_height_caps_cuts_evenly_and_keeps_minimum():
    assert distribute_height_caps([600, 600], 200, 120) == [500, 500]
    assert distribute_height_caps([600, 140], 300, 120) == [320, 120]
    assert distribute_height_caps([100, 100], 50, 120) == [100, 100]
    assert distribute_height_caps([600], 0, 120) == [600]


def test_window_with_many_threads_fits_the_screen_and_scrolls_inside_the_block(tmp_path):
    """Находка ревью: при 40 тредах окно выходило за экран; теперь список ограничен и прокручивается."""
    app, db, w = window(tmp_path, "tall.db")
    w.show()
    app.processEvents()
    w.cache_threads = {"claude": many(40), "codex": many(20, agent="codex")}
    w.cache_expanded = {"claude": True, "codex": True}
    w.cache_show_all = {"claude": True, "codex": True}
    w._render()
    app.processEvents()
    available = w.screen().availableGeometry().height()
    assert w.height() <= available
    claude, codex = w.cache_blocks["claude"], w.cache_blocks["codex"]
    assert claude.list_height() < claude.natural_height() and claude.list_height() >= 3 * ROW_HEIGHT
    assert codex.list_height() >= 3 * ROW_HEIGHT
    w.cache_show_all = {}
    w._render()
    app.processEvents()
    assert w.height() <= available  # свернутые списки по 5 карточек тоже ограничиваются высотой экрана
    assert all(block.list_height() <= block.natural_height() for block in w.cache_blocks.values())
    w.cache_threads = {"claude": many(2), "codex": many(2, agent="codex")}
    w._render()
    app.processEvents()
    assert all(block.list_height() == block.natural_height() for block in w.cache_blocks.values())
    close(app, db, w)


def test_state_dot_colors_and_spinner():
    qapp()
    dot = StateDot()
    dot.set_state(ACTIVITY_NEEDS_REPLY)
    assert dot.activity == ACTIVITY_NEEDS_REPLY and dot.animating is False
    dot.set_state(ACTIVITY_WORKING)
    assert dot.activity == ACTIVITY_WORKING and dot.animating is True
    dot.set_state(ACTIVITY_UNREAD)
    assert dot.animating is False
    dot.set_state(ACTIVITY_WORKING)
    dot.set_state("")
    assert dot.animating is False
    assert set(ACTIVITY_COLORS) == {ACTIVITY_WORKING, ACTIVITY_NEEDS_REPLY, ACTIVITY_UNREAD}
    assert len(set(ACTIVITY_COLORS.values())) == 3


def test_spinner_angle_is_smooth_and_wraps():
    assert spinner_angle(0.0) == 0.0
    assert spinner_angle(SPINNER_PERIOD / 4) == 90.0
    assert spinner_angle(SPINNER_PERIOD / 2) == 180.0
    assert spinner_angle(SPINNER_PERIOD) == 0.0
    assert spinner_angle(SPINNER_PERIOD * 3 + SPINNER_PERIOD / 4) == 90.0
    assert 0.0 <= spinner_angle(12345.678) < 360.0


def test_spinner_clock_is_shared_and_stops_without_dots():
    qapp()
    clock = SpinnerClock.instance()
    for widget in list(clock._dots):  # строки прошлых тестов не показывались и могли оставить подписку
        clock.detach(widget)
    dots = [StateDot() for _ in range(3)]
    for dot in dots:
        dot.set_state(ACTIVITY_WORKING)
    assert clock.running and all(clock.has(dot) for dot in dots)
    assert SpinnerClock.instance() is clock
    dots[0].set_state("")
    assert clock.running and not clock.has(dots[0])
    for dot in dots[1:]:
        dot.set_state(ACTIVITY_NEEDS_REPLY)
    assert not clock.running


def test_spinner_clock_ticks_every_dot_in_one_frame():
    app = qapp()
    painted = []

    class CountingDot(StateDot):
        def paintEvent(self, event):
            painted.append(self)
            super().paintEvent(event)

    holder = QWidget()
    dots = [CountingDot(holder) for _ in range(3)]
    for index, dot in enumerate(dots):
        dot.move(index * 20, 0)
    holder.show()
    app.processEvents()
    for dot in dots:
        dot.set_state(ACTIVITY_WORKING)
    app.processEvents()
    painted.clear()
    SpinnerClock.instance()._tick()
    app.processEvents()
    assert set(painted) == set(dots)
    for dot in dots:
        dot.set_state("")
    holder.close()


def test_state_dot_pauses_spinner_while_hidden():
    qapp()
    dot = StateDot()
    dot.set_state(ACTIVITY_WORKING)
    dot.show()
    assert dot.animating is True
    dot.hide()
    assert dot.animating is False
    dot.show()
    assert dot.animating is True
    dot.set_state(ACTIVITY_NEEDS_REPLY)
    dot.hide()
    dot.show()
    assert dot.animating is False
    dot.close()


def test_state_dot_paints_every_state():
    qapp()
    dot = StateDot()
    empty = dot.grab().toImage()
    images = {}
    for state in (ACTIVITY_WORKING, ACTIVITY_NEEDS_REPLY, ACTIVITY_UNREAD):
        dot.set_state(state)
        images[state] = dot.grab().toImage()
        assert images[state] != empty
    dot.set_state("")
    assert dot.grab().toImage() == empty
    assert images[ACTIVITY_NEEDS_REPLY] != images[ACTIVITY_UNREAD]


def test_row_shows_state_dot_and_tooltip_names_the_state():
    qapp()
    block = CacheThreadsBlock()
    now = utcnow().replace(microsecond=0)
    states = [ACTIVITY_WORKING, ACTIVITY_NEEDS_REPLY, ACTIVITY_UNREAD, ""]
    threads = [replace(thread(f"s{i}", 100), activity=state) for i, state in enumerate(states)]
    block.set_content(threads, now, show_all=True)
    assert [row.dot.activity for row in block.rows] == states
    assert "ждет вашего ответа" in block.rows[1].toolTip() and "не просмотрен" in block.rows[2].toolTip()
    assert "работает" in block.rows[0].toolTip()
    assert not any(text in block.rows[3].toolTip() for text in ("работает", "ждет вашего", "не просмотрен"))
    block.tick(now.replace(second=10))
    block.tick(now.replace(second=11))
    # компактно (по умолчанию) состояние рисует кольцо: блик у работающего, значок у остальных; отдельная точка скрыта
    assert [row.ring.animating for row in block.rows] == [True, False, False, False]
    assert [row.ring.activity for row in block.rows] == states
    assert not any(row.dot.animating for row in block.rows) and all(row.dot.isHidden() for row in block.rows)
    block.set_content(threads, now, show_all=True, view=ThreadView(compact=False))
    assert [row.dot.animating for row in block.rows] == [True, False, False, False]
    for row in block.rows:
        row.dot.set_state("")


# ----------------------------- Ширина окна --------------------------------- #
def mouse_event(kind, global_x, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton):
    point = QPointF(2.0, 10.0)
    return QMouseEvent(kind, point, QPointF(float(global_x), 100.0), button, buttons, Qt.KeyboardModifier.NoModifier)


def expanded_window(tmp_path, name="width.db", threads=5):
    app, db, w = window(tmp_path, name)
    w.show()
    app.processEvents()
    w.cache_threads = {"claude": many(threads)}
    w.toggle_cache_provider("claude")
    app.processEvents()
    return app, db, w


def test_edge_grip_reports_horizontal_drag_and_ignores_other_buttons():
    app = qapp()
    grip = EdgeGrip("left")
    events = []
    grip.drag_started.connect(lambda: events.append("start"))
    grip.dragged.connect(lambda dx: events.append(dx))
    grip.drag_finished.connect(lambda: events.append("end"))
    QApplication.sendEvent(grip, mouse_event(QEvent.Type.MouseMove, 90))
    QApplication.sendEvent(grip, mouse_event(QEvent.Type.MouseButtonPress, 100, Qt.MouseButton.RightButton, Qt.MouseButton.RightButton))
    assert events == [] and grip.dragging is False
    QApplication.sendEvent(grip, mouse_event(QEvent.Type.MouseButtonPress, 100))
    QApplication.sendEvent(grip, mouse_event(QEvent.Type.MouseMove, 70))
    QApplication.sendEvent(grip, mouse_event(QEvent.Type.MouseMove, 130))
    QApplication.sendEvent(grip, mouse_event(QEvent.Type.MouseButtonRelease, 130, buttons=Qt.MouseButton.NoButton))
    assert events == ["start", -30, 30, "end"] and grip.dragging is False
    assert grip.cursor().shape() == Qt.CursorShape.SizeHorCursor
    with pytest.raises(ValueError):
        EdgeGrip("top")


def test_window_has_grips_on_both_side_edges(tmp_path):
    app, db, w = window(tmp_path, "grips.db")
    w.show()
    app.processEvents()
    left, right = w.grips["left"], w.grips["right"]
    assert left.geometry().left() == 0 and right.geometry().right() == w.width() - 1
    assert left.height() == w.height() and right.height() == w.height()
    w.cache_threads = {"claude": many(5)}
    w.toggle_cache_provider("claude")
    app.processEvents()
    assert right.geometry().right() == w.width() - 1 and left.height() == w.height()
    close(app, db, w)


def test_dragging_left_edge_widens_window_keeping_right_edge_and_height(tmp_path):
    app, db, w = expanded_window(tmp_path)
    before = place_bottom_right(w)
    w._grip_started()
    w._grip_dragged("left", -120)
    app.processEvents()
    after = w.geometry()
    assert after.width() == before.width() + 120
    assert (after.right(), after.top(), after.bottom()) == (before.right(), before.top(), before.bottom())
    assert w.user_width == after.width()
    w._grip_finished()
    assert db.get_setting("monitor_width", "") == str(after.width())
    close(app, db, w)


def test_dragging_right_edge_widens_window_keeping_left_edge(tmp_path):
    app, db, w = expanded_window(tmp_path, "right.db")
    area = w.screen().availableGeometry()
    w.move(area.left() + 10, area.top() + 10)
    app.processEvents()
    before = w.geometry()
    w._grip_started()
    w._grip_dragged("right", 90)
    app.processEvents()
    after = w.geometry()
    assert after.left() == before.left() and after.width() == before.width() + 90 and after.height() == before.height()
    close(app, db, w)


def test_width_cannot_go_below_content_or_beyond_the_screen(tmp_path):
    app, db, w = expanded_window(tmp_path, "limits.db")
    before = place_bottom_right(w)
    minimum = w.minimumWidth()
    w._grip_started()
    w._grip_dragged("left", 5000)
    assert w.width() == w.user_width == w.minimumWidth()
    assert w.width() <= minimum
    w._grip_dragged("left", -50000)
    area = w.screen().availableGeometry()
    assert w.geometry().left() >= area.left() and w.geometry().right() == before.right()
    w._grip_finished()
    close(app, db, w)


def test_widening_gives_the_extra_width_to_thread_titles_only(tmp_path):
    app, db, w = expanded_window(tmp_path, "titles.db")
    place_bottom_right(w)
    row = w.cache_blocks["claude"].rows[0]
    metric = provider_row(w, "claude").quota_columns
    narrow = {"title": row.title.width(), "chip": row.ring.width(), "metric": metric.width(), "dot": row.dot.width(),
              "row": provider_row(w, "claude").height()}
    w._grip_started()
    w._grip_dragged("left", -140)
    app.processEvents()
    assert row.title.width() == narrow["title"] + 140
    assert row.ring.width() == narrow["chip"] and row.dot.width() == narrow["dot"]
    assert metric.width() == narrow["metric"] and provider_row(w, "claude").height() == narrow["row"]
    w._grip_finished()
    close(app, db, w)


def test_chosen_width_survives_content_changes_and_restart(tmp_path):
    app, db, w = expanded_window(tmp_path, "persist.db")
    place_bottom_right(w)
    w._grip_started()
    w._grip_dragged("left", -100)
    w._grip_finished()
    chosen = w.width()
    w._cache_scanned({"claude": many(8)})
    app.processEvents()
    assert w.width() == chosen
    w.toggle_cache_provider("claude")
    app.processEvents()
    assert w.width() == chosen
    w.quit_monitor()
    app.processEvents()
    w2 = MonitorWindow(db, auto_refresh=False)
    w2.timer.stop()
    w2.show()
    app.processEvents()
    assert w2.user_width == chosen and w2.width() == chosen
    w2.quit_monitor()
    app.processEvents()
    db.close()


def test_without_chosen_width_window_still_follows_its_content(tmp_path):
    app, db, w = window(tmp_path, "auto.db")
    w.show()
    app.processEvents()
    assert w.user_width == 0
    collapsed = w.width()
    w.cache_threads = {"claude": many(3)}
    w.toggle_cache_provider("claude")
    app.processEvents()
    assert w.user_width == 0 and w.width() >= collapsed
    close(app, db, w)


def test_invalid_saved_width_is_ignored(tmp_path):
    app = qapp()
    db = Database(tmp_path / "bad.db")
    for raw in ("abc", "-5", "10", "99999"):
        db.set_setting("monitor_width", raw)
        w = MonitorWindow(db, auto_refresh=False)
        w.timer.stop()
        assert w.user_width == 0, raw
        w.quit_monitor()
        app.processEvents()
    db.close()


# ------------------- Карточки, вид, закрепить и скрыть ------------ #
def test_toggle_switch_toggles_and_emits():
    qapp()
    switch = ToggleSwitch(True)
    seen = []
    switch.toggled.connect(seen.append)
    assert switch.isChecked() is True
    switch.click()
    switch.click()
    assert seen == [False, True] and switch.isChecked() is True
    on = switch.grab().toImage()
    switch.setChecked(False)
    assert switch.grab().toImage() != on


def test_row_follows_view_options():
    qapp()
    now = utcnow()
    full = CacheThreadRow(thread("a", 100), now)
    full.show()
    assert full.ring is not None and not full.title.isHidden() and not full.project.isHidden()
    assert not full.findChildren(QToolButton)
    assert full.ring.text and full.title._full == "Тред a"
    bare = CacheThreadRow(thread("a", 100), now, ThreadView(timer=False, title=False, project=False, pin_hide=False))
    bare.show()
    assert bare.ring is None and bare.title.isHidden() and bare.project.isHidden()
    assert not bare.findChildren(QToolButton)
    bare.refresh(now)  # без кольца обновление не падает
    only_title = CacheThreadRow(thread("a", 100), now, ThreadView(timer=False, project=False))
    only_title.show()
    assert not only_title.title.isHidden() and only_title.project.isHidden() and only_title.ring is None


@pytest.mark.parametrize("compact", [True, False])
@pytest.mark.parametrize("rules_enabled", [True, False])
def test_thread_row_has_no_pin_or_hide_buttons(compact, rules_enabled):
    qapp()
    row = CacheThreadRow(thread("t7", 100), utcnow(), ThreadView(compact=compact, pin_hide=rules_enabled), pinned=True)
    assert row.pinned is True
    assert not row.findChildren(QToolButton)


def test_block_passes_view_pins_and_restore_link():
    qapp()
    block = CacheThreadsBlock()
    now = utcnow()
    threads = many(3)
    events = []
    block.restore_clicked.connect(lambda: events.append("restore"))
    assert block.set_content(threads, now, view=ThreadView(), pinned={"t1"}, hidden_count=2) is True
    assert block.rows[1].pinned is True and block.rows[0].pinned is False
    assert block.restore is not None and block.restore.text() == "Вернуть скрытые (2)"
    block.show()
    QTest.mouseClick(block.restore, Qt.MouseButton.LeftButton)
    assert events == ["restore"]
    assert block.set_content(threads, now, view=ThreadView(), pinned={"t1"}, hidden_count=2) is False
    assert block.set_content(threads, now, view=ThreadView(timer=False), pinned={"t1"}, hidden_count=2) is True
    assert block.rows[0].ring is None
    assert block.set_content(threads, now, view=ThreadView(pin_hide=False), pinned={"t1"}, hidden_count=2) is True
    assert block.restore is None and not block.rows[0].findChildren(QToolButton)
    assert block.set_content(threads, now, view=ThreadView(), pinned=set(), hidden_count=0) is True and block.restore is None
    assert block.set_content([], now, view=ThreadView(), hidden_count=3) is True
    assert block.note is not None and block.restore is not None  # все скрыты: подсказка и ссылка «вернуть»


def test_removed_thread_rows_are_hidden_before_deferred_deletion():
    app = qapp()
    now = utcnow()
    block = CacheThreadsBlock()
    block.set_content([thread("old", 100)], now)
    block.show()
    app.processEvents()
    old_row = block.rows[0]
    assert old_row.isVisible()
    block.set_content([thread("new", 100)], now)
    assert old_row.isHidden()
    assert block.thread_keys == ["new"]


def test_menu_opens_the_drawer_with_view_switches(tmp_path):
    app, db, w = window(tmp_path, "drawer.db")
    w.show()
    app.processEvents()
    assert not w.drawer.isVisible()
    menu = w.menu_button.menu()
    assert menu is w.tray.contextMenu()
    action = next(a for a in menu.actions() if a.text() == "Настроить вид")
    geometry_closed = w.geometry()
    QTimer.singleShot(0, lambda: QTest.mouseClick(menu, Qt.MouseButton.LeftButton,
                                                pos=menu.actionGeometry(action).center()))
    QTest.mouseClick(w.menu_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert w.drawer.isVisible() is True and w.drawer.isWindow() and w.geometry() == geometry_closed
    assert not w.drawer.geometry().intersects(w.frameGeometry())
    assert set(w.view_switches) == {"compact", "timer", "title", "project", "rewrite", "warning", "glutton", "pin_hide", "terminal", "forecast"}
    assert all(switch.isChecked() for name, switch in w.view_switches.items() if name != "terminal")
    assert not w.view_switches["terminal"].isChecked()
    assert {name: combo.currentData() for name, combo in w.view_combos.items()} == {"usage": "both", "chart": "click", "font": "0"}
    chart = w.view_combos["chart"]
    assert chart.accessibleName() == "График расхода"
    assert [chart.itemText(i) for i in range(chart.count())] == ["по щелчку", "выключен"]
    QTest.mouseClick(w.drawer.findChild(QToolButton, "PopupClose"), Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not w.drawer.isVisible() and w.geometry() == geometry_closed
    close(app, db, w)


def test_menu_opens_and_closes_with_the_keyboard(tmp_path):
    app, db, w = window(tmp_path, "menu.db")
    w.show()
    app.processEvents()
    menu = w.menu_button.menu()
    opened = []

    def dismiss():
        opened.append(menu.isVisible())
        QTest.keyClick(menu, Qt.Key.Key_Escape)

    w.menu_button.setFocus()
    QTimer.singleShot(0, dismiss)
    QTest.keyClick(w.menu_button, Qt.Key.Key_Space)
    app.processEvents()
    assert opened == [True] and not menu.isVisible()
    close(app, db, w)


def test_opacity_slider_in_the_panel_applies_and_remembers(tmp_path):
    app, db, w = window(tmp_path, "opacity.db")
    w.show()
    w.toggle_drawer()
    app.processEvents()
    assert w.opacity_slider.value() == w.opacity // 10
    w.opacity_slider.setValue(3)
    assert w.opacity == 30 and db.get_setting("monitor_opacity") == "30" and w.opacity_value.text() == "30%"
    assert abs(w.windowOpacity() - 0.3) < 0.01
    w.opacity_slider.setValue(0)  # ниже 20% не опускается
    assert w.opacity == 20
    w.hide_to_tray()
    assert not w.drawer.isVisible() and not w.isVisible()
    close(app, db, w)


def test_view_switches_change_rows_and_survive_restart(tmp_path):
    app, db, w = window(tmp_path, "view.db")
    w.show()
    w.cache_threads = {"claude": many(2)}
    w.toggle_cache_provider("claude")
    app.processEvents()
    rows = lambda: w.cache_blocks["claude"].rows
    assert all(row.ring is not None and not row.title.isHidden() for row in rows())
    w.toggle_drawer()
    QTest.mouseClick(w.view_switches["timer"], Qt.MouseButton.LeftButton)
    app.processEvents()
    assert w.thread_view.timer is False and all(row.ring is None for row in rows())
    QTest.keyClick(w.view_switches["title"], Qt.Key.Key_Space)
    QTest.mouseClick(w.view_switches["project"], Qt.MouseButton.LeftButton)
    app.processEvents()
    assert all(row.title.isHidden() and row.project.isHidden() for row in rows())
    assert json.loads(db.get_setting("monitor_thread_view")) == {
        "chart": "click", "compact": True, "font": "0", "forecast": True, "glutton": True, "pin_hide": True, "project": False, "rewrite": True,
        "terminal": False, "timer": False, "title": False, "usage": "both", "warning": True,
    }
    w.quit_monitor()
    app.processEvents()
    w2 = MonitorWindow(db, auto_refresh=False)
    w2.timer.stop()
    assert w2.thread_view == ThreadView(timer=False, title=False, project=False)
    assert [name for name, switch in w2.view_switches.items() if not switch.isChecked()] == ["timer", "title", "project", "terminal"]
    w2.quit_monitor()
    app.processEvents()
    db.close()


def test_existing_pin_and_hide_rules_are_preserved_and_restorable(tmp_path):
    app, db, w = window(tmp_path, "rules.db")
    w.show()
    w.cache_threads = {"claude": many(3)}
    w.toggle_cache_provider("claude")
    app.processEvents()
    block = lambda: w.cache_blocks["claude"]
    assert block().thread_keys == ["t0", "t1", "t2"] and block().restore is None
    db.set_pinned("t2", True)
    w._reload_thread_rules()
    app.processEvents()
    assert block().thread_keys == ["t2", "t0", "t1"] and block().rows[0].pinned is True
    assert db.thread_rules()["t2"]["pinned"] is True
    db.set_hidden("t0", True)
    w._reload_thread_rules()
    app.processEvents()
    assert block().thread_keys == ["t2", "t1"] and block().restore.text() == "Вернуть скрытые (1)"
    assert db.thread_rules()["t0"]["hidden"] is True
    # выключенная функция правила не применяет, но и не стирает их
    w.view_switches["pin_hide"].setChecked(False)
    app.processEvents()
    assert block().thread_keys == ["t0", "t1", "t2"] and block().restore is None
    assert db.thread_rules()["t0"]["hidden"] is True and db.thread_rules()["t2"]["pinned"] is True
    w.view_switches["pin_hide"].setChecked(True)
    app.processEvents()
    assert block().thread_keys == ["t2", "t1"]
    QTest.mouseClick(block().restore, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert block().thread_keys == ["t2", "t0", "t1"] and "t0" not in db.thread_rules()
    db.set_pinned("t2", False)
    w._reload_thread_rules()
    app.processEvents()
    assert block().thread_keys == ["t0", "t1", "t2"] and "t2" not in db.thread_rules()
    close(app, db, w)


def test_pinned_thread_stays_visible_beyond_the_short_list_and_rules_survive_restart(tmp_path):
    app, db, w = window(tmp_path, "pinned.db")
    w.show()
    w.cache_threads = {"claude": many(8)}
    w.toggle_cache_provider("claude")
    app.processEvents()
    assert len(w.cache_blocks["claude"].rows) == 3
    db.set_pinned("t7", True)
    db.set_hidden("t0", True)
    w._reload_thread_rules()
    app.processEvents()
    assert w.cache_blocks["claude"].thread_keys[0] == "t7" and "t0" not in w.cache_blocks["claude"].thread_keys
    assert w.cache_blocks["claude"].link.text() == "Показать еще 4"
    w.quit_monitor()
    app.processEvents()
    w2 = MonitorWindow(db, auto_refresh=False)
    w2.timer.stop()
    w2.cache_threads = {"claude": many(8)}
    w2._render()
    app.processEvents()
    assert w2.cache_blocks["claude"].thread_keys[0] == "t7" and w2.cache_blocks["claude"].restore.text() == "Вернуть скрытые (1)"
    w2.quit_monitor()
    app.processEvents()
    db.close()


# --------------- Макет: проект · среда и предупреждение об остывании ---------- #
def test_row_shows_project_and_environment_in_one_line_without_pill():
    qapp()
    detailed = ThreadView(compact=False)
    row = CacheThreadRow(thread("a", 100), utcnow(), detailed)
    assert row.project._full == "demo_shop · Desktop" and not hasattr(row, "env")
    bare = CacheThreadRow(replace(thread("a", 100), environment=""), utcnow(), detailed)
    assert bare.project._full == "demo_shop"


def test_row_warns_shortly_before_the_cache_goes_cold():
    qapp()
    now = utcnow()
    soon = CacheThreadRow(thread("a", 300 - 100, ttl=300), now)  # осталось 100 секунд
    soon.show()
    assert soon.warning is True and soon.property("warn") is True
    assert "Остынет через" in soon.warn_label.text() and soon.warn_label.text().endswith("Нужно ли написать?")
    assert not CacheThreadRow(thread("a", 100, ttl=3600), now).warning  # срока еще много
    assert not CacheThreadRow(thread("a", 400, ttl=300), now).warning  # уже остыл
    edge = CacheThreadRow(thread("a", 300 - WARN_SECONDS - 5, ttl=300), now)
    assert edge.warning is False
    off = CacheThreadRow(thread("a", 300 - 100, ttl=300), now, ThreadView(warning=False))
    assert off.warning is False and off.property("warn") in (None, False)
    # предупреждение появляется само, когда срок подходит к концу, и пропадает после остывания
    row = CacheThreadRow(thread("a", 300 - WARN_SECONDS - 30, ttl=300), now)
    assert row.warning is False
    assert row.refresh(now + dt.timedelta(seconds=40)) is True and row.warning is True
    assert row.refresh(now + dt.timedelta(seconds=41)) is False
    assert row.refresh(now + dt.timedelta(seconds=WARN_SECONDS + 60)) is True and row.warning is False


def test_block_tick_reports_layout_change_when_a_warning_appears():
    app = qapp()
    block = CacheThreadsBlock()
    now = utcnow()
    changes = []
    block.content_changed.connect(lambda: changes.append(1))
    block.set_content([thread("a", 300 - WARN_SECONDS - 30, ttl=300)], now)
    block.tick(now + dt.timedelta(seconds=5))
    assert changes == []
    block.tick(now + dt.timedelta(seconds=40))
    assert changes == [1]
    block.tick(now + dt.timedelta(seconds=41))
    assert changes == [1]
    assert block.set_content([thread("a", 300 - WARN_SECONDS - 30, ttl=300)], now + dt.timedelta(seconds=300)) is True


def test_window_grows_when_a_warning_row_appears_and_shrinks_back(tmp_path):
    app, db, w = window(tmp_path, "warn.db")
    w.show()
    now = utcnow()
    w.cache_threads = {"claude": [thread("a", 300 - WARN_SECONDS - 30, ttl=300), thread("b", 100)]}
    w.toggle_cache_provider("claude")
    QTest.qWait(120)  # окно успевает подогнать высоту под вложенные раскладки
    height = w.height()
    block = w.cache_blocks["claude"]
    block.tick(now + dt.timedelta(seconds=40))
    app.processEvents()
    assert block.rows[0].warning is True and w.height() > height
    block.tick(now + dt.timedelta(seconds=WARN_SECONDS + 60))
    app.processEvents()
    assert block.rows[0].warning is False and w.height() == height
    w.view_switches["warning"].setChecked(True)
    close(app, db, w)


def test_window_is_never_shorter_than_its_content_after_expanding(tmp_path):
    """Находка по снимку экрана: нижняя строка (подвал) обрезалась на 8 пикселей после первого разворачивания."""
    app, db, w = window(tmp_path, "short.db")
    w.show()
    app.processEvents()
    w.cache_threads = {"claude": many(2)}
    w.toggle_cache_provider("claude")
    QTest.qWait(120)
    layout = w.centralWidget().layout()
    assert w.height() >= max(layout.totalMinimumSize().height(), w.centralWidget().minimumSizeHint().height())
    close(app, db, w)


# --------------------------- Расход, перезапись, прогноз --------------------------- #
from claude_usage_manager.thread_usage import ThreadSpend  # noqa: E402
from claude_usage_manager.cache_threads_qt import glutton_id  # noqa: E402
from claude_usage_manager.models import QuotaSnapshot, QuotaWindow  # noqa: E402
from claude_usage_manager.quota_monitor_model import MonitorData, ProviderState, build_compact_rows, build_rows  # noqa: E402
from claude_usage_manager.forecast_qt import ForecastBar  # noqa: E402


def chips(row):
    return [chip.label.text() for chip in (row.share_chip, row.cost_chip, row.rewrite_chip) if not chip.isHidden()]


def test_row_shows_spend_chips_like_the_mockup():
    qapp()
    now = utcnow()
    row = CacheThreadRow(thread("a", 100), now, ThreadView(compact=False), spend=ThreadSpend(3.4, 10, 0, 2.1, 0.74))
    assert chips(row) == ["~2,1% нед", "$3,40", "перезапись $0,74"]
    assert row.glutton_row.isHidden()
    assert row.update_thread(thread("a", 100), now, ThreadSpend(3.4, 10, 0, 2.1, 0.74), glutton=True) is True
    assert not row.glutton_row.isHidden() and row.glutton_chip.label.text() == "прожорливый"
    pct = CacheThreadRow(thread("a", 100), now, ThreadView(compact=False, usage="pct", rewrite=False), spend=ThreadSpend(3.4, 10, 0, 2.1, 0.74))
    assert chips(pct) == ["~2,1% нед"]
    usd = CacheThreadRow(thread("a", 100), now, ThreadView(usage="usd"), spend=ThreadSpend(0, 10, 10, None, None))
    assert chips(usd) == ["нет цены"]
    assert not row.findChildren(QToolButton)
    off = CacheThreadRow(thread("a", 100), now, ThreadView(compact=False, usage="off", rewrite=False, pin_hide=False), spend=ThreadSpend(3.4, 10, 0, 2.1, 0.74))
    assert chips(off) == [] and off.chips_row.isHidden()
    no_chips = CacheThreadRow(thread("a", 100), now, ThreadView(compact=False, usage="off", rewrite=False))
    assert chips(no_chips) == [] and no_chips.chips_row.isHidden()
    nothing = CacheThreadRow(thread("a", 100), now, ThreadView(compact=False, pin_hide=False))
    assert nothing.chips_row.isHidden()


def test_compact_row_is_two_lines_with_project_and_short_chips():
    qapp()
    now = utcnow()
    row = CacheThreadRow(thread("a", 100), now, ThreadView(), spend=ThreadSpend(3.4, 10, 0, 2.1, 0.74))
    assert row.compact and row.ring.width() == COMPACT_RING_SIZE and row.minimumHeight() == COMPACT_ROW_HEIGHT
    assert row.ring._font_pt == 5.0 and row.ring._bold is False  # цифры в кольце мелкие и не жирные
    # проект без среды стоит в строке чипов, перед ними; среда и пояснения в подсказке
    assert row.project.parentWidget() is row.chips_row and row.project._full == "demo_shop"
    assert "Desktop" in row.toolTip()
    assert chips(row) == ["~2,1%", "$3,40", "$0,74"]
    assert "перезапис" in row.rewrite_chip.toolTip()
    assert not row.findChildren(QToolButton)
    assert row.update_thread(thread("a", 100), now, ThreadSpend(3.4, 10, 0, 2.1, 0.74), glutton=True) is True
    assert not row.glutton_mini.isHidden() and row.glutton_row.isHidden()
    assert "Прожорливый" in row.glutton_mini.toolTip()
    tiny = CacheThreadRow(thread("a", 100), now, ThreadView(), spend=ThreadSpend(0.01, 10, 0, 0.01, None))
    assert chips(tiny)[0] == "<0,1%"
    # Без проекта и показателей пустая строка не занимает место.
    bare = CacheThreadRow(thread("a", 100), now, ThreadView(project=False, usage="off", rewrite=False))
    assert row.chips_row.isVisibleTo(row) and bare.project.isHidden() and bare.chips_row.isHidden()


def test_compact_rows_line_up_title_project_ring_and_chip_columns():
    app = qapp()
    block = CacheThreadsBlock()
    threads = [replace(thread("a", 100), activity=ACTIVITY_WORKING), thread("b", 110), thread("c", 120)]
    spend = {"a": ThreadSpend(0.23, 1, 0, 0.01, 0.23), "b": ThreadSpend(109.19, 1, 0, 3.5, 1.81), "c": ThreadSpend(12.43, 1, 0, None, 6.57)}
    block.set_content(threads, utcnow(), spend=spend)
    block.resize(380, 400)
    block.show()
    app.processEvents()
    origin = lambda widget, row: widget.mapTo(row, QPoint(0, 0)).x()
    for row in block.rows:
        # название и проект с одного края: точка состояния висит в своем столбце слева
        assert origin(row.project, row) == origin(row.title, row)
        # кольцо по центру двух строк
        assert abs(row.ring.geometry().center().y() - row.rect().center().y()) <= 1
    for name in ("share_chip", "cost_chip", "rewrite_chip"):
        chips = [getattr(row, name) for row in block.rows]
        assert len({origin(chip, row) for chip, row in zip(chips, block.rows)}) == 1, name
        assert len({chip.width() for chip in chips}) == 1, name
    # у треда без доли место чипа пустое, столбцы не съезжают; пламя прожорливого - в строке названия
    assert block.rows[2].share_chip.isHidden()
    assert not block.rows[1].glutton_mini.isHidden() and block.rows[1].glutton_mini.parentWidget() is block.rows[1]
    block.deleteLater()
    app.processEvents()


def test_glutton_is_the_biggest_share_and_needs_a_comparison():
    threads = [thread("a", 10), thread("b", 20), thread("c", 30)]
    spend = {"a": ThreadSpend(1, 1, 0, 2.0, None), "b": ThreadSpend(5, 1, 0, 7.8, None), "c": ThreadSpend(0, 1, 1, None, None)}
    assert glutton_id(threads, spend) == "b"
    assert glutton_id(threads, {"b": spend["b"]}) is None
    block = CacheThreadsBlock()
    block.set_content(threads, utcnow(), spend=spend, view=ThreadView(compact=False))
    assert [not row.glutton_row.isHidden() for row in block.rows] == [False, True, False]
    block.set_content(threads, utcnow(), spend=spend, view=ThreadView(compact=False, glutton=False))
    assert not any(not row.glutton_row.isHidden() for row in block.rows)
    # компактно: значок пламени в строке чипов вместо отдельной строки
    block.set_content(threads, utcnow(), spend=spend)
    assert [not row.glutton_mini.isHidden() for row in block.rows] == [False, True, False]
    assert all(row.glutton_row.isHidden() for row in block.rows)


def week_states(used=70.0):
    now = utcnow()
    snapshot = QuotaSnapshot(provider="claude", source="codexbar:claude", updated_at=now, main_window_id="secondary", windows=[
        QuotaWindow("primary", "5h", 38, resets_at=now + dt.timedelta(hours=3), window_minutes=300, role="primary"),
        QuotaWindow("secondary", "week", used, resets_at=now + dt.timedelta(days=3), window_minutes=10080, role="secondary"),
    ])
    return {"claude": ProviderState("claude", snapshot, True)}


def forecast_window(tmp_path, name="forecast.db"):
    app, db, w = window(tmp_path, name)
    states = week_states()
    w.states = states
    w.rows = build_rows(states)
    compact, selected = build_compact_rows(db, states)
    w.data = MonitorData(states, w.rows, compact, selected)
    w.cache_threads = {"claude": many(2)}
    w._spend = {"claude": {"t0": ThreadSpend(3.4, 10, 0, 2.1, 0.74), "t1": ThreadSpend(1.0, 5, 0, 0.6, 0.12)}}
    w._render()
    w.show()
    w.toggle_cache_provider("claude")
    app.processEvents()
    return app, db, w


def wait_for(condition, timeout=2000):
    for _ in range(timeout // 10):
        if condition():
            return
        QTest.qWait(10)
    assert condition()


def test_large_thread_list_keeps_visible_rows_and_keyboard_focus(tmp_path):
    app, db, w = window(tmp_path)
    w.cache_threads = {"claude": many(150), "codex": many(3, "codex")}
    w.show()
    w.toggle_cache_provider("claude")
    w.toggle_cache_provider("codex")
    app.processEvents()
    block = w.cache_blocks["claude"]
    first_rows = block.rows[:]
    neighbor_rows = w.cache_blocks["codex"].rows[:]
    link = block.link
    link.setFocus()
    QTest.keyClick(link, Qt.Key.Key_Space)
    assert 3 < len(block.rows) < 150
    assert block.rows[:3] == first_rows and link.hasFocus()
    wait_for(lambda: len(block.rows) == 150)
    assert block.thread_keys == [f"t{i}" for i in range(150)]
    assert block._scroll.verticalScrollBar().maximum() > 0
    assert w.height() <= w.screen().availableGeometry().height()
    assert w.cache_blocks["codex"].rows == neighbor_rows

    QTest.keyClick(link, Qt.Key.Key_Return)
    assert block.rows == first_rows and link.hasFocus()
    QTest.keyClick(link, Qt.Key.Key_Space)
    QTest.keyClick(link, Qt.Key.Key_Return)
    QTest.qWait(80)
    assert block.rows == first_rows and link.hasFocus()
    close(app, db, w)


def test_disclosure_remains_usable_while_forecast_history_is_loading(tmp_path, monkeypatch):
    app, db, w = window(tmp_path)
    states = week_states()
    compact, selected = build_compact_rows(db, states)
    w.data = MonitorData(states, build_rows(states), compact, selected)
    w._render()
    w.show()
    w.activateWindow()
    app.processEvents()
    started, release = Event(), Event()
    original = db.quota_samples

    def load_samples(**kwargs):
        started.set()
        if not release.wait(3):
            raise RuntimeError("Загрузка истории не была разблокирована")
        return original(**kwargs)

    monkeypatch.setattr(db, "quota_samples", load_samples)
    disclosure = w.header_rows["claude"].disclosure
    try:
        QTest.mouseClick(disclosure, Qt.MouseButton.LeftButton)
        assert started.wait(1)
        assert w.forecast_bars["claude"].forecast.method == "average"
        assert "claude" in w._forecast_workers and "claude" not in w._forecasts
        disclosure.setFocus()
        QTest.keyClick(disclosure, Qt.Key.Key_Space)
        assert "claude" not in w.cache_blocks and disclosure.hasFocus()
    finally:
        release.set()
    wait_for(lambda: "claude" in w._forecasts)
    assert "claude" not in w.forecast_bars
    close(app, db, w)


def test_forecast_uses_week_samples_from_the_database_with_a_weighted_rate(tmp_path):
    app, db, w = forecast_window(tmp_path, "weighted.db")
    bar = w.forecast_bars["claude"]
    wait_for(lambda: "claude" in w._forecasts)
    window = w.weekly_of("claude")
    start = window.resets_at.astimezone(dt.timezone.utc) - dt.timedelta(days=7)
    for hours, used in ((24, 10), (72, 20), (95, 70)):
        snapshot = QuotaSnapshot(provider="claude", source="claude-code:official-mod", windows=[replace(window, used_percent=used)])
        db._conn.execute(
            "INSERT INTO quota_samples (ts_utc,weekly,source,windows_json) VALUES (?,?,?,?)",
            ((start + dt.timedelta(hours=hours)).isoformat(), used, "claude-code:official-mod", db._windows_json(snapshot)),
        )
    db._conn.commit()
    assert bar.forecast.method == "average"
    w._forecasts.clear()
    w._refresh_forecasts()
    wait_for(lambda: bar.forecast.method == "weighted")
    forecast = bar.forecast
    assert forecast.method == "weighted" and forecast.rate_per_day > 17.5  # последние часы быстрее среднего
    assert "открыть расход за неделю по проектам" in w.forecast_bars["claude"].toolTip()
    QTest.mouseClick(w.forecast_bars["claude"].chart_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert w.forecast_popup.tiles[1][1].text().startswith("~") and "свежие часы весят больше" in w.forecast_popup.note.text()
    close(app, db, w)

def test_expanded_card_has_forecast_and_spend_and_the_popup_follows_the_chart_mode(tmp_path):
    app, db, w = forecast_window(tmp_path)
    bar = w.forecast_bars["claude"]
    wait_for(lambda: "claude" in w._forecasts)
    assert isinstance(bar, ForecastBar) and "кончится" in bar.end_label.text()
    assert "открыть расход за неделю по проектам" in bar.toolTip()
    assert not hasattr(bar, "status") and not hasattr(bar, "start_label")  # строки над полосой и дня начала недели нет
    rows = w.cache_blocks["claude"].rows
    assert chips(rows[0]) == ["~2,1%", "$3,40", "$0,74"]  # компактный вид по умолчанию
    assert not rows[0].glutton_mini.isHidden() and rows[1].glutton_mini.isHidden()
    button = bar.chart_button
    pos = center_in_window(w, bar)
    assert w.childAt(pos) is button
    QTest.mouseMove(w.windowHandle(), pos)
    QTest.mouseClick(w.windowHandle(), Qt.MouseButton.RightButton, pos=pos)
    QTest.mouseMove(w.windowHandle(), center_in_window(w, w.footer))
    QTest.qWait(350)
    assert w.forecast_popup is None

    QTest.mouseClick(w.windowHandle(), Qt.MouseButton.LeftButton, pos=center_in_window(w, bar))
    app.processEvents()
    popup = w.forecast_popup
    assert popup.isVisible() and button.isChecked()
    assert popup.tiles[0][1].text() == "70%" and popup.tiles[1][1].text() == "17,5% в сутки"
    QTest.mouseMove(w.windowHandle(), center_in_window(w, w.footer))
    QTest.qWait(350)
    assert popup.isVisible()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert not popup.isVisible() and not button.isChecked()

    QTest.keyClick(button, Qt.Key.Key_Space)
    assert popup.isVisible()
    QTest.keyClick(popup.close_button, Qt.Key.Key_Escape)
    app.processEvents()
    assert not popup.isVisible() and button.hasFocus()
    QTest.keyClick(button, Qt.Key.Key_Return)
    assert popup.isVisible()
    QTest.mouseClick(popup.close_button, Qt.MouseButton.LeftButton)
    assert not popup.isVisible()

    w.toggle_drawer()
    chart_mode = w.view_combos["chart"]
    QTest.keyClick(chart_mode, Qt.Key.Key_End)
    assert json.loads(db.get_setting("monitor_thread_view"))["chart"] == "off"
    assert not button.isEnabled()
    QTest.mouseMove(w.windowHandle(), pos)
    QTest.mouseClick(w.windowHandle(), Qt.MouseButton.LeftButton, pos=pos)
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert not popup.isVisible()
    QTest.keyClick(chart_mode, Qt.Key.Key_Home)
    assert w.thread_view.chart == "click" and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert popup.isVisible()
    QTest.keyClick(chart_mode, Qt.Key.Key_End)
    assert not popup.isVisible() and not button.isChecked()
    w._set_thread_view("forecast", False)
    assert "claude" not in w.forecast_bars
    close(app, db, w)


def test_open_chart_follows_only_an_explicit_choice_of_agent(tmp_path):
    app, db, w = forecast_window(tmp_path, "two-weeks.db")
    states = dict(w.states)
    weekly = replace(w.weekly_of("claude"), used_percent=42)
    snapshot = QuotaSnapshot(provider="codex", windows=[weekly])
    states["codex"] = ProviderState("codex", snapshot, True)
    compact, selected = build_compact_rows(db, states)
    w._done(MonitorData(states, build_rows(states), compact, selected))
    QTest.mouseClick(w.header_rows["codex"].disclosure, Qt.MouseButton.LeftButton)
    app.processEvents()
    claude = w.forecast_bars["claude"].chart_button
    codex = w.forecast_bars["codex"].chart_button

    QTest.mouseClick(claude, Qt.MouseButton.LeftButton)
    popup = w.forecast_popup
    QTest.mouseMove(w.windowHandle(), center_in_window(w, codex))
    QTest.mouseClick(codex, Qt.MouseButton.RightButton)
    assert popup.isVisible() and popup.tiles[0][1].text() == "70%"
    assert claude.isChecked() and not codex.isChecked()
    QTest.mouseClick(codex, Qt.MouseButton.LeftButton)
    assert popup is w.forecast_popup and popup.tiles[0][1].text() == "42%"
    assert not claude.isChecked() and codex.isChecked()
    w._render()
    assert popup.isVisible() and codex.isChecked()
    QTest.mouseClick(w.header_rows["codex"].disclosure, Qt.MouseButton.LeftButton)
    assert not popup.isVisible()
    close(app, db, w)


def test_collapsed_card_has_no_forecast(tmp_path):
    app, db, w = forecast_window(tmp_path, "collapsed.db")
    w.toggle_cache_provider("claude")
    app.processEvents()
    assert w.forecast_bars == {} and w.cache_blocks == {}
    close(app, db, w)


def test_agent_name_shows_running_thread_count(tmp_path):
    app, db, w = window(tmp_path, "running.db")
    w.show()
    app.processEvents()
    header = provider_row(w, "claude")
    assert header.name_label.text() == "Claude Code"  # сканирования еще не было: скобок нет
    working = [replace(thread("a", 10), activity=ACTIVITY_WORKING), replace(thread("b", 20), activity=ACTIVITY_WORKING), thread("c", 30)]
    w._cache_scanned({"claude": working, "codex": [], "grok": []})
    app.processEvents()
    assert provider_row(w, "claude").name_label.text() == "Claude Code (2)"
    assert provider_row(w, "codex").name_label.text() == "Codex (0)"
    assert provider_row(w, "antigravity").name_label.text() == "Antigravity"
    close(app, db, w)


def test_collapsed_agents_are_scanned_only_in_the_live_window(tmp_path):
    app, db, w = window(tmp_path, "scan.db")
    assert w._cache_wanted() is False  # тестовое окно без сканирования журналов
    w._cache_scan = True
    assert w._cache_wanted() is True and w._usage_wanted() is False  # счетчик нужен всегда, расход - только у развернутого списка
    w._cache_scan = False
    close(app, db, w)


def test_full_reset_expiring_within_two_weeks_is_highlighted():
    from claude_usage_manager.models import ManualResetCredits, ManualResetItem
    from claude_usage_manager.quota_monitor import expiring_reset

    qapp()
    now = utcnow()
    def credits(days):
        return ManualResetCredits(provider="claude", state="available", available_count=1,
                                  items=[ManualResetItem("one", "claude", now + dt.timedelta(days=days))])
    assert expiring_reset(credits(20), now) is None
    assert expiring_reset(credits(13), now) is not None
    assert expiring_reset(credits(-1), now) is None
    assert expiring_reset(ManualResetCredits(provider="claude", state="available", available_count=0), now) is None
    states = week_states()
    states["claude"].snapshot.manual_resets = credits(9)
    row = build_rows(states)["claude"][0]
    header = CompactRow(row, states["claude"], now=now)
    assert header.reset_notice is not None and header.reset_notice.property("critical") is False
    assert header.reset_notice.text().startswith("Есть полный сброс: сгорит") and "через 9 дн." in header.reset_notice.text()
    states["claude"].snapshot.manual_resets = credits(2)
    assert CompactRow(row, states["claude"], now=now).reset_notice.property("critical") is True
    two = ManualResetCredits(provider="claude", state="available", available_count=2,
                             items=[ManualResetItem("one", "claude", now + dt.timedelta(days=11)), ManualResetItem("two", "claude", now + dt.timedelta(days=20))])
    states["claude"].snapshot.manual_resets = two
    assert CompactRow(row, states["claude"], now=now).reset_notice.text().startswith("Полных сбросов: 2, ближайший сгорит")
    states["claude"].snapshot.manual_resets = credits(30)
    assert CompactRow(row, states["claude"], now=now).reset_notice is None


def test_font_size_setting_scales_the_window_style(tmp_path):
    from claude_usage_manager.quota_monitor import STYLE, scaled_style

    title_rule = lambda style: style.split("QLabel#CacheTitle")[1].split("}")[0]
    assert scaled_style(0) == STYLE
    assert "font-size:10.25pt" in title_rule(STYLE)  # названия тредов на полпункта крупнее остального текста
    assert "font-size:10.75pt" in title_rule(scaled_style(0.5))
    app, db, w = window(tmp_path, "font.db")
    assert w.font_delta() == 0.0 and "font-size:10.25pt" in title_rule(w.styleSheet())
    w.view_combos["font"].setCurrentIndex(w.view_combos["font"].findData("+2"))
    assert w.thread_view.font == "+2" and "font-size:12.25pt" in title_rule(w.styleSheet())
    assert w.drawer.styleSheet() == w.styleSheet()
    close(app, db, w)
