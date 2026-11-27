import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

from PySide6.QtCore import QPoint,QRect,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QDialogButtonBox,QLabel,QMessageBox
from claude_usage_manager.database import Database
from claude_usage_manager.models import ManualResetCredits,ManualResetItem,QuotaSnapshot,QuotaWindow
from claude_usage_manager.qt_theme import build_stylesheet
from claude_usage_manager.quota_monitor import COMPACT_MIN_HEIGHT,COMPACT_MIN_WIDTH,MONITOR_NAME,MONITOR_VERSION,STYLE,MonitorWindow,normalize_cached_reset,short_reset,state_status
from claude_usage_manager.quota_monitor_model import ProviderState,build_compact_rows,build_rows,cached_states,manual_reset_tooltip,manual_reset_urgency,remaining,refresh_states,select_antigravity_window
import claude_usage_manager.quota_monitor_model as quota_monitor_model


def qapp(): return QApplication.instance() or QApplication([])
def snap(provider,windows,main="",login=""):
    return QuotaSnapshot(provider=provider,source=f"codexbar:{provider}",login_method=login,windows=windows,main_window_id=main,updated_at=dt.datetime(2026,8,11,15,0,tzinfo=dt.timezone.utc))

def test_remaining_is_inverse_used_percent():
    assert remaining(QuotaWindow("a","A",8.0))==92.0
    assert remaining(QuotaWindow("a","A",100.0))==0.0
    assert remaining(QuotaWindow("a","A",0.0))==100.0


def test_refresh_updates_existing_cards_without_recreating_widgets(tmp_path):
    from claude_usage_manager.quota_monitor_model import MonitorData

    app=qapp();db=Database(tmp_path/"ui.db")
    now=dt.datetime.now(dt.timezone.utc)
    def data(percent,live):
        snapshot=QuotaSnapshot(provider="grok",source="grok:local-billing-log",updated_at=now,
            windows=[QuotaWindow("grok:weekly","Grok",percent,resets_at=now+dt.timedelta(days=3),window_minutes=10080)],
            main_window_id="grok:weekly")
        states={"grok":ProviderState("grok",snapshot,live)}
        rows=build_rows(states);compact,selected=build_compact_rows(db,states)
        return MonitorData(states,rows,compact,selected)
    w=MonitorWindow(db,auto_refresh=False);w.timer.stop()
    try:
        w.data=data(10,True);w._render();w.show();app.processEvents()
        header=w.header_rows["grok"];card=header.parentWidget();dot=header.dot
        w.data=data(25,False);w._render();app.processEvents()
        assert w.header_rows["grok"] is header and header.parentWidget() is card and header.dot is dot
        assert "75%" in header.summary_text and dot.objectName()=="CacheDot"
        w._render();assert w.header_rows["grok"] is header
    finally:
        w.quit_monitor();db.close()


def test_subscription_payments_save_cancel_restore_and_clear(tmp_path, monkeypatch):
    from claude_usage_manager.quota_monitor import PAYMENTS_SETTING, SettingsDialog

    app = qapp()
    local_now = dt.datetime(2026, 11, 25, 12).astimezone()
    monkeypatch.setattr('claude_usage_manager.quota_monitor.now_local', lambda: local_now)
    monkeypatch.setattr(quota_monitor_model, 'read_best_local_claude_quota', lambda: QuotaSnapshot(provider='claude'))
    db = Database(tmp_path / 'ui.db')
    window = MonitorWindow(db, auto_refresh=False)
    window.show()
    header = window.header_rows['claude']
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))

    def edit(dialog, date, amount, button):
        date_field, amount_field = dialog.payment_fields['claude']
        date_field.setText(date)
        amount_field.setText(amount)
        dialog.findChild(QDialogButtonBox).button(button).click()

    def edit_and_save(dialog):
        save = QDialogButtonBox.StandardButton.Save
        edit(dialog, '31.02', '20 $', save)
        assert dialog.result() == QDialog.DialogCode.Rejected
        edit(dialog, '04.11', '', save)
        assert dialog.result() == QDialog.DialogCode.Rejected
        edit(dialog, '04.11', '20 $', save)
        return dialog.result()

    try:
        assert header.payment_label.isHidden()
        monkeypatch.setattr(SettingsDialog, 'exec', edit_and_save)
        window.open_settings()
        app.processEvents()
        assert len(warnings) == 2
        assert window.header_rows['claude'] is header
        assert header.payment_label.text() == '04.12 · 20 $'
        assert 'Следующий платеж 04.12.2026' in header.payment_label.toolTip()
        assert window.header_rows['codex'].payment_label.isHidden()
        saved = db.get_setting(PAYMENTS_SETTING)
        assert json.loads(saved) == {'claude': {'date': '2026-11-04', 'amount': '20 $'}}
        local_now = dt.datetime(2026, 12, 4, 0, 1).astimezone()
        window._update_clock()
        assert header.payment_label.text() == '04.01 · 20 $'
        assert '04.01.2027' in header.payment_label.toolTip()
        assert db.get_setting(PAYMENTS_SETTING) == saved

        def clear(dialog, button):
            assert [field.text() for field in dialog.payment_fields['claude']] == ['04.11', '20 $']
            edit(dialog, '', '', button)
            return dialog.result()

        monkeypatch.setattr(SettingsDialog, 'exec', lambda dialog: clear(dialog, QDialogButtonBox.StandardButton.Cancel))
        window.open_settings()
        assert not header.payment_label.isHidden()

        reopened = MonitorWindow(db, auto_refresh=False)
        try:
            assert reopened.header_rows['claude'].payment == (dt.date(2026, 11, 4), '20 $')
            assert '04.01.2027' in reopened.header_rows['claude'].payment_label.toolTip()
            monkeypatch.setattr(SettingsDialog, 'exec', lambda dialog: clear(dialog, QDialogButtonBox.StandardButton.Save))
            reopened.open_settings()
            assert reopened.header_rows['claude'].payment_label.isHidden()
            assert json.loads(db.get_setting(PAYMENTS_SETTING)) == {}
        finally:
            reopened.quit_monitor()
    finally:
        window.quit_monitor()
        db.close()


def test_manual_reset_tooltip_sorts_all_dates_and_marks_urgency():
    now=dt.datetime(2026,9,4,12,0,tzinfo=dt.timezone.utc)
    credits=ManualResetCredits(
        provider="codex",state="available",available_count=3,
        items=[
            ManualResetItem("late","codex",now+dt.timedelta(days=5)),
            ManualResetItem("urgent","codex",now+dt.timedelta(hours=23)),
            ManualResetItem("middle","codex",now+dt.timedelta(days=2)),
        ],
    )

    tooltip=manual_reset_tooltip(credits,now=now)
    positions=[tooltip.index(value.astimezone().strftime("%d.%m.%Y %H:%M")) for value in (
        now+dt.timedelta(hours=23),now+dt.timedelta(days=2),now+dt.timedelta(days=5),
    )]
    assert positions==sorted(positions)
    assert manual_reset_urgency(credits,now=now)=="critical"
    credits.items=[ManualResetItem("warning","codex",now+dt.timedelta(days=2))]
    assert manual_reset_urgency(credits,now=now)=="warning"
    credits.items=[ManualResetItem("normal","codex",now+dt.timedelta(days=4))]
    assert manual_reset_urgency(credits,now=now)=="normal"


@pytest.mark.parametrize("state,count,expected_text,has_suffix", [
    ("none",0,"нед 90%",False),
    ("unknown",None,"нед 90%",False),
    ("available",1,"нед 90%/1",True),
    ("available",3,"нед 90%/3",True),
])
def test_compact_manual_reset_suffix_only_when_positive(state,count,expected_text,has_suffix):
    from claude_usage_manager.quota_monitor import CompactRow

    qapp()
    snapshot=snap("codex",[QuotaWindow("primary","week",10,window_minutes=10080,role="primary")],"primary")
    snapshot.manual_resets=ManualResetCredits(
        provider="codex",state=state,available_count=count,
        items=[ManualResetItem("credit","codex",dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=5))] if count else [],
    )
    provider_state=ProviderState("codex",snapshot,True)
    row=build_rows({"codex":provider_state})["codex"][0]
    widget=CompactRow(row,provider_state)
    assert widget.name_label.text()=="Codex"
    assert widget.summary_text==expected_text
    assert ("Кредиты ручного сброса" in widget.quota_columns.toolTip()) is has_suffix
    if has_suffix:
        assert f"Кредиты ручного сброса: {count}" in widget.quota_columns.toolTip()


def test_claude_header_shows_both_limits_like_the_mockup_and_lists_each_expiry():
    from claude_usage_manager.quota_monitor import CompactRow

    qapp()
    now=dt.datetime.now(dt.timezone.utc)
    first=now+dt.timedelta(days=2)
    second=now+dt.timedelta(days=5)
    snapshot=snap("claude",[
        QuotaWindow("primary","5h",10,window_minutes=300,role="primary"),
        QuotaWindow("secondary","week",20,window_minutes=10080,role="secondary"),
    ],"secondary","Claude Max")
    snapshot.manual_resets=ManualResetCredits(
        provider="claude",state="available",available_count=2,
        items=[ManualResetItem("later","claude",second),ManualResetItem("sooner","claude",first)],
        source="claude-web-session:organization-usage",freshness="live",
    )
    state=ProviderState("claude",snapshot,True)
    row=build_rows({"claude":state})["claude"][0]
    widget=CompactRow(row,state)
    assert widget.name_label.text()=="Claude Code"
    assert widget.summary_text=="5 ч 90% · нед 80%/2"
    assert "font-weight:600" in widget.quota_columns.headlines[0].text() and ">90%<" in widget.quota_columns.headlines[0].text()
    from claude_usage_manager.quota_columns import remaining_color
    assert remaining_color(90) == remaining_color(50) != remaining_color(49) != remaining_color(19)
    tip=widget.quota_columns.toolTip()
    assert "5 ч: осталось 90%" in tip and "Неделя: осталось 80%" in tip
    assert "Кредиты ручного сброса: 2" in tip
    first_text=first.astimezone().strftime("%d.%m.%Y %H:%M")
    second_text=second.astimezone().strftime("%d.%m.%Y %H:%M")
    assert tip.index(first_text)<tip.index(second_text)


def test_claude_codex_compact_short_long():
    states={
      "claude":ProviderState("claude",snap("claude",[QuotaWindow("primary","5h",8,window_minutes=300,role="primary"),QuotaWindow("secondary","week",33,window_minutes=10080,role="secondary")],"secondary","Claude Max 5x"),True),
      "codex":ProviderState("codex",snap("codex",[QuotaWindow("primary","5h",0,window_minutes=300,role="primary"),QuotaWindow("secondary","week",4,window_minutes=10080,role="secondary")],"secondary","Pro Lite"),True),
    }
    rows=build_rows(states)
    assert rows["claude"][0].name=="Claude"
    assert rows["claude"][0].subtitle=="Claude Max 5x"
    assert rows["claude"][0].short.remaining==92
    assert rows["claude"][0].long.remaining==67
    assert rows["codex"][0].short.remaining==100
    assert rows["codex"][0].long.remaining==96


def test_antigravity_models_independent_and_fixed_selection(tmp_path):
    db=Database(tmp_path/"q.db")
    state=ProviderState("antigravity",snap("antigravity",[QuotaWindow("model-a","Gemini 3.1 Pro (High)",25,role="model"),QuotaWindow("model-b","Claude Sonnet 4.6",60,role="model")],"model-a","Pro"),True)
    rows=build_rows({"antigravity":state})["antigravity"]
    assert [r.short.remaining for r in rows]==[40,75]
    db.set_setting("monitor_ag_mode","fixed");db.set_setting("monitor_ag_fixed_model","model-b")
    chosen=select_antigravity_window(db,state.snapshot)
    assert chosen.id=="model-b"
    compact,selected=build_compact_rows(db,{"antigravity":state})
    assert selected=="model-b"
    assert compact[-1].subtitle=="Claude Sonnet 4.6"
    assert compact[-1].short.remaining==40
    db.close()

def test_antigravity_console_primary_reaches_both_views(tmp_path):
    db=Database(tmp_path/"q.db")
    state=ProviderState("antigravity",snap("antigravity",[
        QuotaWindow("primary","Лимит модели",6,resets_at=dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1),role="primary")
    ],"primary","Pro"),True)
    rows=build_rows({"antigravity":state})["antigravity"]
    compact,selected=build_compact_rows(db,{"antigravity":state})
    assert len(rows)==1
    assert rows[0].short.remaining==94
    assert selected=="primary"
    assert compact[-1].short.remaining==94
    db.close()

def test_antigravity_most_consumed_selection(tmp_path):
    db=Database(tmp_path/"q.db");db.set_setting("monitor_ag_mode","most_consumed")
    s=snap("antigravity",[QuotaWindow("model-a","A",20,role="model"),QuotaWindow("model-b","B",70,role="model")])
    assert select_antigravity_window(db,s).id=="model-b"
    db.close()

def test_cached_snapshot_survives_outage(tmp_path):
    db=Database(tmp_path/"q.db");q=snap("grok",[QuotaWindow("grok:weekly","Grok · общий недельный лимит",20,resets_at=dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=3),window_minutes=10080,role="group")],"grok:weekly","Grok Build");q.source="grok:local-billing-log";assert db.record_quota(q)
    state=cached_states(db)["grok"]
    assert not state.live and state.snapshot.has_data()
    assert build_rows({"grok":state})["grok"][0].long.remaining==80
    db.close()


def test_manual_resets_survive_cache_without_quota_window(tmp_path):
    db=Database(tmp_path/"q.db")
    snapshot=QuotaSnapshot(
        provider="codex",source="codexbar:codex",
        manual_resets=ManualResetCredits(
            provider="codex",state="available",available_count=1,
            source="codex-app-server:account/rateLimits/read",freshness="live",
            updated_at=dt.datetime.now(dt.timezone.utc),
            items=[ManualResetItem("credit","codex",dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=2))],
        ),
    )
    assert db.record_quota(snapshot)

    cached=cached_states(db)["codex"].snapshot
    assert not cached.has_data()
    assert cached.manual_resets.available_count==1
    assert cached.manual_resets.freshness=="cached"
    db.close()

def test_informational_snapshot_is_not_live_data():
    snapshot=QuotaSnapshot(provider="antigravity",windows=[QuotaWindow("primary","Нет данных",0,role="primary",informational=True)])
    assert not snapshot.has_data()

def test_cached_reset_merge_ignores_expired_values_and_keeps_future():
    from claude_usage_manager.quota_monitor_model import _merge_cached_resets
    now=dt.datetime.now(dt.timezone.utc)
    current=QuotaSnapshot(provider="claude",windows=[QuotaWindow("primary","5 ч",8,window_minutes=300,role="primary")])
    cached=QuotaSnapshot(provider="claude",windows=[QuotaWindow("primary","5 ч",8,resets_at=now-dt.timedelta(minutes=1),window_minutes=300,role="primary")])
    _merge_cached_resets(current,cached)
    assert current.window("primary").resets_at is None

    future=now+dt.timedelta(hours=2)
    cached.window("primary").resets_at=future
    _merge_cached_resets(current,cached)
    assert current.window("primary").resets_at==future

def test_local_history_state_is_not_live(monkeypatch,tmp_path):
    db=Database(tmp_path/"q.db")
    snapshot=snap("claude",[QuotaWindow("primary","5 ч",6,window_minutes=300,role="primary")],"primary","Claude Code")
    snapshot.fallback_source="Локальная история Claude Code"
    monkeypatch.setattr(quota_monitor_model,"fetch_quota",lambda _provider:snapshot)
    states=refresh_states(db)
    assert not states["claude"].live
    assert "локальная история" in states["claude"].error.casefold()
    assert state_status(states["claude"])=="локальные данные"
    db.close()

def test_cached_snapshot_skips_antigravity_offline_placeholder(tmp_path):
    db=Database(tmp_path/"q.db")
    valid=snap("antigravity",[QuotaWindow("ag-group:gemini:weekly","Gemini Models",4,window_minutes=10080,role="group")],"ag-group:gemini:weekly","Antigravity")
    placeholder=QuotaSnapshot(
        provider="antigravity",source="codexbar:antigravity",login_method="offline",
        windows=[QuotaWindow("primary","Нет данных",0,role="primary",informational=True)],
        main_window_id="primary",updated_at=dt.datetime(2026,8,12,15,0,tzinfo=dt.timezone.utc),
    )
    assert db.record_quota(valid)
    assert db.record_quota(placeholder)

    state=cached_states(db)["antigravity"]
    compact,selected=build_compact_rows(db,{"antigravity":state})

    assert not state.live
    assert state.snapshot.main_window_id=="ag-group:gemini:weekly"
    assert selected=="ag-group:gemini:weekly"
    assert compact[-1].short.remaining==96
    db.close()

def test_cached_antigravity_reset_stays_factual():
    updated=dt.datetime(2026,8,12,6,45,tzinfo=dt.timezone.utc)
    reset=dt.datetime(2026,8,12,11,45,tzinfo=dt.timezone.utc)
    now=dt.datetime(2026,8,13,12,36,tzinfo=dt.timezone.utc)
    shown=normalize_cached_reset(reset,"antigravity",False,updated,now)
    assert shown==reset
    assert normalize_cached_reset(reset,"antigravity",True,updated,now)==reset
    assert normalize_cached_reset(reset,"codex",False,updated,now)==reset


def test_antigravity_compact_shows_both_weekly_groups(tmp_path):
    db=Database(tmp_path/"q.db")
    gemini_reset=dt.datetime(2026,8,15,20,21,tzinfo=dt.timezone.utc)
    other_reset=dt.datetime(2026,8,20,14,59,tzinfo=dt.timezone.utc)
    state=ProviderState("antigravity",snap("antigravity",[
        QuotaWindow("ag-group:gemini:weekly","Gemini Models",4.12,resets_at=gemini_reset,window_minutes=10080,role="group"),
        QuotaWindow("ag-group:gemini:5h","Gemini Models",1.48,resets_at=dt.datetime(2026,8,13,15,6,tzinfo=dt.timezone.utc),window_minutes=300,role="group"),
        QuotaWindow("ag-group:claude-gpt:weekly","Claude and GPT models",12,resets_at=other_reset,window_minutes=10080,role="group"),
    ]),True)
    rows=build_rows({"antigravity":state})["antigravity"]
    assert [row.name for row in rows]==["Gemini Models","Claude and GPT models"]
    compact,selected=build_compact_rows(db,{"antigravity":state})
    row=compact[-1]
    assert selected==""
    assert row.short.label=="Gemini Models"
    assert row.short.remaining==95.88
    assert row.short.resets_at==gemini_reset
    assert row.long.label=="Claude and GPT models"
    assert row.long.remaining==88
    assert row.long.resets_at==other_reset
    db.close()


def test_grouped_antigravity_compact_ignores_old_single_group_setting(tmp_path):
    db=Database(tmp_path/"q.db")
    db.set_setting("monitor_ag_mode","most_consumed")
    state=ProviderState("antigravity",snap("antigravity",[
        QuotaWindow("ag-group:gemini:weekly","Gemini Models",4,resets_at=dt.datetime(2026,8,15,tzinfo=dt.timezone.utc),window_minutes=10080,role="group"),
        QuotaWindow("ag-group:claude-gpt:weekly","Claude and GPT models",12,resets_at=dt.datetime(2026,8,20,tzinfo=dt.timezone.utc),window_minutes=10080,role="group"),
    ]),True)
    compact,selected=build_compact_rows(db,{"antigravity":state})
    row=compact[-1]
    assert selected==""
    assert row.short.label=="Gemini Models"
    assert row.short.remaining==96
    assert row.long.label=="Claude and GPT models"
    assert row.long.remaining==88
    db.close()

def test_compact_window_defaults_and_settings(tmp_path):
    app=qapp();db=Database(tmp_path/"ui.db");db.set_setting("monitor_refresh_seconds","120");db.set_setting("monitor_always_on_top","1");db.set_setting("monitor_opacity","20");db.set_setting("monitor_compact_visibility",'{"compact:claude": false}')
    w=MonitorWindow(db,auto_refresh=False);w.timer.stop();app.processEvents()
    assert w.windowTitle()==f"{MONITOR_NAME} {MONITOR_VERSION}"
    assert w.minimumWidth()>=COMPACT_MIN_WIDTH and w.minimumWidth()<320
    assert w.minimumHeight()>=COMPACT_MIN_HEIGHT
    assert w.opacity==20 and w.topmost is True and w.seconds==120
    assert w.visibility["compact:claude"] is False
    assert bool(w.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert w.actions.isVisible() is False
    w.quit_monitor();app.processEvents();db.close()

def test_compact_actions_remain_visible_over_header_and_hide_outside(tmp_path):
    app=qapp();db=Database(tmp_path/"ui-hover.db")
    w=MonitorWindow(db,auto_refresh=False);w.timer.stop();w.show();app.processEvents()
    w._update_actions_visibility(w.frameGeometry().topLeft())
    assert w.actions.isVisible()
    outside=w.frameGeometry().bottomRight()+QPoint(20,20)
    w._update_actions_visibility(outside)
    assert not w.actions.isVisible()
    w.quit_monitor();app.processEvents();db.close()

def test_compact_window_reapplies_transparency_surface(tmp_path):
    app=qapp();db=Database(tmp_path/"ui-opacity.db");db.set_setting("monitor_opacity","20")
    w=MonitorWindow(db,auto_refresh=False);w.timer.stop();app.processEvents()
    shell=w.centralWidget()
    assert abs(w.windowOpacity()-0.2)<0.01  # прозрачно все окно, а не фон: над полупрозрачным фоном текст мылится
    assert w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert shell.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert shell.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
    assert shell.COLOR.alpha()==255  # подложка плотная и рисуется вручную: фон из стиля у прозрачного окна не рисуется
    w.opacity=80;w._apply_window_flags();w._apply_opacity();app.processEvents()
    assert abs(w.windowOpacity()-0.8)<0.01
    w.quit_monitor();app.processEvents();db.close()


def test_compact_window_text_sits_on_opaque_pixels(tmp_path):
    # ClearType у текста только над непрозрачными пикселями: при любой настройке прозрачности фон под текстом плотный
    app=qapp();db=Database(tmp_path/"ui-solid.db");db.set_setting("monitor_opacity","40")
    w=MonitorWindow(db,auto_refresh=False);w.timer.stop();w.show();app.processEvents()
    image=w.grab().toImage()
    center=image.pixelColor(image.width()//2,image.height()//2)
    assert center.alpha()==255
    w.quit_monitor();app.processEvents();db.close()


def test_quota_popups_removed_from_compact_monitor():
    source=pathlib.Path("claude_usage_manager/quota_monitor.py").read_text(encoding="utf-8")
    assert "class CursorPopup" not in source
    assert "class AntigravityPopup" not in source
    assert "QScrollArea" not in source
    assert "_row_hovered" not in source

def test_monitor_entry_point_is_unified():
    text=pathlib.Path("monitor.py").read_text(encoding="utf-8")
    assert "unified_monitor import main" in text
    assert "app.py" not in text


def test_compact_rows_have_no_provider_popup_state(tmp_path):
    app=qapp();db=Database(tmp_path/"ui.db")
    reset=dt.datetime(2026,8,20,14,59,tzinfo=dt.timezone.utc)
    states={
        "grok":ProviderState("grok",snap("grok",[
            QuotaWindow("grok:weekly","Grok",10,resets_at=reset,window_minutes=10080,role="group"),
        ]),True),
        "antigravity":ProviderState("antigravity",snap("antigravity",[
            QuotaWindow("ag-group:gemini:weekly","Gemini Models",4,resets_at=reset,window_minutes=10080,role="group"),
            QuotaWindow("ag-group:claude-gpt:weekly","Claude and GPT models",12,resets_at=reset,window_minutes=10080,role="group"),
        ]),True),
    }
    w=MonitorWindow(db,auto_refresh=False);w.timer.stop()
    w.states=states;w.rows=build_rows(states);compact,selected=build_compact_rows(db,states)
    from claude_usage_manager.quota_monitor_model import MonitorData
    w.data=MonitorData(states,w.rows,compact,selected);w._render();app.processEvents()
    assert not hasattr(w,"_popup")
    assert len(w.findChildren(__import__('claude_usage_manager.quota_monitor',fromlist=['CompactRow']).CompactRow))==2
    w.quit_monitor();app.processEvents();db.close()



def test_compact_ui_shows_agent_cards_by_name_like_the_mockup(tmp_path):
    app=qapp();app.setStyleSheet(build_stylesheet()+STYLE);db=Database(tmp_path/"ui.db")
    reset=dt.datetime(2026,8,18,12,36,tzinfo=dt.datetime.now().astimezone().tzinfo)
    states={
      "claude":ProviderState("claude",snap("claude",[
          QuotaWindow("primary","5h",3,resets_at=reset,window_minutes=300,role="primary"),
          QuotaWindow("secondary","week",44,resets_at=reset,window_minutes=10080,role="secondary"),
      ],"secondary","Claude Max 5x"),True),
      "codex":ProviderState("codex",snap("codex",[
          QuotaWindow("secondary","week",92,resets_at=reset,window_minutes=10080,role="secondary"),
      ],"secondary","Pro Lite"),True),
      "grok":ProviderState("grok",snap("grok",[
          QuotaWindow("grok:weekly","Grok · общий недельный лимит",51,resets_at=reset,window_minutes=10080,role="group"),
      ],"grok:weekly","Grok Build"),False),
      "antigravity":ProviderState("antigravity",snap("antigravity",[
          QuotaWindow("ag-group:gemini:weekly","Gemini Models",0,resets_at=reset,window_minutes=10080,role="group"),
          QuotaWindow("ag-group:claude-gpt:weekly","Claude and GPT models",20,resets_at=reset,window_minutes=10080,role="group"),
      ]),False),
    }
    states["codex"].snapshot.manual_resets=ManualResetCredits(
        provider="codex",state="available",available_count=2,
        source="codex-app-server:account/rateLimits/read",freshness="live",
        items=[
            ManualResetItem("one","codex",dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=12)),
            ManualResetItem("two","codex",dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=2)),
        ],
    )
    from claude_usage_manager.quota_monitor import AgentCard,CompactRow
    from claude_usage_manager.quota_monitor_model import MonitorData
    w=MonitorWindow(db,auto_refresh=False);w.timer.stop()
    w.states=states;w.rows=build_rows(states);compact,selected=build_compact_rows(db,states)
    w.data=MonitorData(states,w.rows,compact,selected);w._render();w.show()
    w.centralWidget().layout().activate();QTest.qWait(10);app.processEvents()

    rows=w.findChildren(CompactRow)
    assert len(rows)==4 and len(w.findChildren(AgentCard))==4
    by_provider={row.row.provider:row for row in rows}
    assert {p:row.name_label.text() for p,row in by_provider.items()}=={"claude":"Claude Code","codex":"Codex","grok":"Grok Build","antigravity":"Antigravity"}
    assert by_provider["claude"].summary_text=="5 ч 97% · нед 56%"
    assert by_provider["codex"].summary_text=="нед 8%/2"
    assert by_provider["grok"].summary_text=="нед 49%"
    # у Antigravity названия моделей не пишутся: основная модель и прочие, только проценты (названия в подсказке)
    assert by_provider["antigravity"].summary_text=="100% · 80%"
    assert "Gemini Models" in by_provider["antigravity"].quota_columns.toolTip()
    assert all(row.icon.pixmap() is not None and row.icon.width()==18 for row in rows)
    assert by_provider["grok"].row.long.window_id=="grok:weekly"
    assert "сброс" in by_provider["claude"].quota_columns.toolTip()
    assert not hasattr(w,"_popup")
    row_rects=sorted((QRect(row.mapTo(w,QPoint(0,0)),row.size()) for row in rows),key=lambda rect:rect.y())
    assert all(left.bottom()<right.top() for left,right in zip(row_rects,row_rects[1:]))
    for row in rows:
        row_rect=QRect(row.mapTo(w,QPoint(0,0)),row.size())
        for label in (row.icon,row.name_label,row.quota_columns):
            assert row_rect.contains(QRect(label.mapTo(w,QPoint(0,0)),label.size()))
            assert label.sizeHint().width()<=label.width()
    w.quit_monitor();app.processEvents();db.close()


def test_compact_monitor_renders_without_clipping_at_requested_offscreen_high_dpi(tmp_path):
    code="""
import datetime as dt
import json
import tempfile
from pathlib import Path
from PySide6.QtCore import QPoint,QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from claude_usage_manager.database import Database
from claude_usage_manager.models import QuotaSnapshot,QuotaWindow
from claude_usage_manager.qt_theme import build_stylesheet
import claude_usage_manager.quota_monitor as monitor
from claude_usage_manager.quota_monitor import CompactRow,MonitorWindow,PAYMENTS_SETTING,STYLE
from claude_usage_manager.quota_monitor_model import MonitorData,ProviderState,build_compact_rows,build_rows
app=QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(build_stylesheet()+STYLE)
def snap(provider,windows,main='',login=''):
    return QuotaSnapshot(provider=provider,source=f'codexbar:{provider}',login_method=login,windows=windows,main_window_id=main,updated_at=dt.datetime(2026,8,11,15,0,tzinfo=dt.timezone.utc))
reset=dt.datetime(2026,8,18,12,36,tzinfo=dt.datetime.now().astimezone().tzinfo)
states={
 'claude':ProviderState('claude',snap('claude',[QuotaWindow('primary','5h',3,resets_at=reset,window_minutes=300,role='primary'),QuotaWindow('secondary','week',44,resets_at=reset,window_minutes=10080,role='secondary')],'secondary','Claude Max 5x'),True),
 'codex':ProviderState('codex',snap('codex',[QuotaWindow('secondary','week',9,resets_at=reset,window_minutes=10080,role='secondary')],'secondary','Pro Lite'),True),
}
with tempfile.TemporaryDirectory() as temp:
    db=Database(Path(temp)/'ui.db')
    monitor.now_local = lambda: dt.datetime(2026, 12, 25, 12).astimezone()
    db.set_setting(PAYMENTS_SETTING, json.dumps({p:{'date':'2023-01-09','amount':'20 $'} for p in states}))
    w=MonitorWindow(db,auto_refresh=False)
    w.timer.stop();w.states=states;w.rows=build_rows(states);compact,selected=build_compact_rows(db,states);w.data=MonitorData(states,w.rows,compact,selected);w._render();w.show();QTest.qWait(20);app.processEvents()
    rows=w.findChildren(CompactRow)
    row_rects=sorted((QRect(row.mapTo(w,QPoint(0,0)),row.size()) for row in rows),key=lambda rect:rect.y())
    no_clip=True
    for row in rows:
        row_rect=QRect(row.mapTo(w,QPoint(0,0)),row.size())
        for label in (row.icon,row.name_label,row.quota_columns,row.payment_label):
            no_clip=no_clip and row_rect.contains(QRect(label.mapTo(w,QPoint(0,0)),label.size())) and label.sizeHint().width()<=label.width()
        no_clip=no_clip and row.payment_label.text()=='09.01 · 20 $'
        no_clip=no_clip and row.name_label.mapTo(w,QPoint(0,row.name_label.height())).y()<=row.payment_label.mapTo(w,QPoint(0,0)).y()
        no_clip=no_clip and row.payment_label.mapTo(w,QPoint(row.payment_label.width(),0)).x()<=row.quota_columns.mapTo(w,QPoint(0,0)).x()
    print(json.dumps({'screen_dpr':app.primaryScreen().devicePixelRatio(),'rows':len(rows),'no_overlap':all(left.bottom()<right.top() for left,right in zip(row_rects,row_rects[1:])),'no_clip':no_clip,'width':w.width(),'height':w.height()}))
    w.quit_monitor();app.processEvents();db.close()
"""
    env=os.environ.copy();env['QT_QPA_PLATFORM']='offscreen';env['QT_SCALE_FACTOR']='1.5'
    env['LOCALAPPDATA']=str(tmp_path)  # журнал и данные приложения подпроцесса - не в живом каталоге виджета (CLA-8)
    result=subprocess.run([sys.executable,'-c',code],cwd=pathlib.Path.cwd(),env=env,capture_output=True,text=True,check=True,timeout=30)
    payload=json.loads(result.stdout)
    assert payload['screen_dpr']>=1.5 and payload['rows']==2
    assert payload['no_overlap'] and payload['no_clip']
    assert payload['width']<560 and payload['height']<300  # offscreen без системных шрифтов: буквы-квадраты шире настоящих


def test_claude_desktop_indicator_explains_local_snapshot_without_live_claim():
    from claude_usage_manager.quota_monitor import CompactRow
    from claude_usage_manager.models import QuotaSnapshot, QuotaWindow
    from claude_usage_manager.quota_monitor_model import ProviderState, build_rows

    qapp()
    now = dt.datetime.now(dt.timezone.utc)
    snapshot = QuotaSnapshot(
        provider="claude", source="claude-desktop:local-history",
        updated_at=now, main_window_id="secondary",
        fallback_source="Claude Desktop · локальные данные",
        windows=[
            QuotaWindow("primary", "Пятичасовое окно", 2,
                        window_minutes=300, role="primary"),
            QuotaWindow("secondary", "Недельная квота", 3,
                        window_minutes=10080, role="secondary"),
        ],
    )
    state = ProviderState("claude", snapshot, False)
    row = build_rows({"claude": state})["claude"][0]
    widget = CompactRow(row, state)
    dot = next(x for x in widget.findChildren(QLabel) if x.objectName() == "LocalDot")
    assert "Claude Desktop" in dot.toolTip()
    assert "Последнее измерение" in dot.toolTip()
    assert "без запросов к Anthropic" in dot.toolTip()
    widget.deleteLater()


@pytest.mark.parametrize("delta,expected", [
    (dt.timedelta(days=3,hours=8,minutes=42), "3д 8ч"),
    (dt.timedelta(days=6,hours=23,minutes=59), "6д 23ч"),
    (dt.timedelta(days=1), "1д 0ч"),
    (dt.timedelta(hours=8,minutes=34,seconds=50), "8ч 34м"),
    (dt.timedelta(hours=1), "1ч 0м"),
    (dt.timedelta(minutes=34,seconds=10), "34м"),
    (dt.timedelta(seconds=20), "1м"),
    (dt.timedelta(0), ""),
    (dt.timedelta(minutes=-5), ""),
])
def test_reset_countdown_short_days_hours_minutes(delta,expected):
    from claude_usage_manager.quota_columns import reset_countdown

    now=dt.datetime(2026,11,13,8,26,tzinfo=dt.timezone.utc)
    assert reset_countdown(now+delta,now)==expected
    assert reset_countdown(None,now)==""


def _claude_state(now,short_reset,long_reset):
    snapshot=snap("claude",[
        QuotaWindow("primary","5h",18,resets_at=short_reset,window_minutes=300,role="primary"),
        QuotaWindow("secondary","week",53,resets_at=long_reset,window_minutes=10080,role="secondary"),
    ],"secondary","Claude Max")
    return ProviderState("claude",snapshot,True)


def test_header_shows_reset_countdown_under_each_percent():
    from claude_usage_manager.quota_monitor import CompactRow

    qapp()
    now=dt.datetime(2026,11,13,8,26,tzinfo=dt.timezone.utc)
    state=_claude_state(now,now+dt.timedelta(hours=3,minutes=14),now+dt.timedelta(days=3,hours=8,minutes=42))
    row=build_rows({"claude":state})["claude"][0]
    widget=CompactRow(row,state,now=now)
    columns = widget.quota_columns
    assert '>82%<' in columns.headlines[0].text() and '>47%<' in columns.headlines[1].text()
    assert [label.text() for label in columns.countdowns] == ['3ч 14м', '3д 8ч']
    # текст заголовка и подсказка прежние
    assert widget.summary_text=="5 ч 82% · нед 47%"
    assert "сброс" in widget.quota_columns.toolTip()


def test_header_without_future_reset_stays_one_line():
    from claude_usage_manager.quota_monitor import CompactRow

    qapp()
    now=dt.datetime(2026,11,13,8,26,tzinfo=dt.timezone.utc)
    for short_reset,long_reset in ((None,None),(now-dt.timedelta(minutes=1),now-dt.timedelta(hours=2))):
        state=_claude_state(now,short_reset,long_reset)
        row=build_rows({"claude":state})["claude"][0]
        widget=CompactRow(row,state,now=now)
        assert all(label.isHidden() for label in widget.quota_columns.countdowns)


def test_header_countdown_skips_window_without_reset():
    from claude_usage_manager.quota_monitor import CompactRow

    qapp()
    now=dt.datetime(2026,11,13,8,26,tzinfo=dt.timezone.utc)
    state=_claude_state(now,None,now+dt.timedelta(hours=8,minutes=34))
    row=build_rows({"claude":state})["claude"][0]
    widget=CompactRow(row,state,now=now)
    assert widget.quota_columns.countdowns[0].isHidden()
    assert widget.quota_columns.countdowns[1].text() == "8ч 34м"


def test_header_countdown_ticks_by_minute_and_reports_text_change():
    from claude_usage_manager.quota_monitor import CompactRow

    qapp()
    now=dt.datetime(2026,11,13,8,26,tzinfo=dt.timezone.utc)
    state=_claude_state(now,now+dt.timedelta(minutes=2,seconds=30),None)
    row=build_rows({"claude":state})["claude"][0]
    widget=CompactRow(row,state,now=now)
    assert widget.quota_columns.countdowns[0].text() == "2м"
    # внутри минуты текст тот же: ни setText, ни подгонки окна
    assert widget.refresh_countdown(now+dt.timedelta(seconds=20)) is False
    assert widget.quota_columns.countdowns[0].text() == "2м"
    # смена минуты меняет текст, а с ним может смениться ширина: окну нужна подгонка
    assert widget.refresh_countdown(now+dt.timedelta(minutes=1)) is True
    assert widget.quota_columns.countdowns[0].text() == "1м"
    assert widget.refresh_countdown(now+dt.timedelta(minutes=1,seconds=10)) is False
    # срок прошел: строка срока пропадает
    assert widget.refresh_countdown(now+dt.timedelta(minutes=3)) is True
    assert all(label.isHidden() for label in widget.quota_columns.countdowns)


def test_header_countdown_follows_font_delta_suffix_and_groups(tmp_path):
    from claude_usage_manager.quota_monitor import CompactRow

    qapp();db=Database(tmp_path/"ui.db")
    now=dt.datetime(2026,11,13,8,26,tzinfo=dt.timezone.utc)
    # сдвиг шрифта: срок 8 пт плюс сдвиг, и после тика размер тот же
    state=_claude_state(now,now+dt.timedelta(hours=3,minutes=14,seconds=30),now+dt.timedelta(days=3,hours=8,minutes=42))
    widget=CompactRow(build_rows({"claude":state})["claude"][0],state,font_delta=1.5,now=now)
    assert widget.quota_columns.countdowns[0].text() == "3ч 14м"
    assert "9.5pt" in widget.quota_columns.countdowns[0].styleSheet()
    widget.refresh_countdown(now+dt.timedelta(minutes=1))
    assert widget.quota_columns.countdowns[0].text() == "3ч 13м"
    # суффикс /N: срок под процентом с кредитами, текст заголовка прежний
    codex=snap("codex",[QuotaWindow("primary","week",42,resets_at=now+dt.timedelta(days=3,hours=8,minutes=42),window_minutes=10080,role="primary")],"primary")
    codex.manual_resets=ManualResetCredits(provider="codex",state="available",available_count=2,items=[ManualResetItem("credit","codex",now+dt.timedelta(days=20))])
    codex_state=ProviderState("codex",codex,True)
    codex_row=CompactRow(build_rows({"codex":codex_state})["codex"][0],codex_state,now=now)
    columns = codex_row.quota_columns
    assert codex_row.summary_text=="нед 58%/2" and ">58%/2<" in columns.headlines[0].text()
    assert columns.countdowns[0].text() == "3д 8ч"
    # Antigravity: под каждой группой свой срок
    ag_state=ProviderState("antigravity",snap("antigravity",[
        QuotaWindow("ag-group:gemini:weekly","Gemini Models",0,resets_at=now+dt.timedelta(days=1,minutes=5),window_minutes=10080,role="group"),
        QuotaWindow("ag-group:claude-gpt:weekly","Claude and GPT models",20,resets_at=now+dt.timedelta(days=6,hours=23,minutes=59),window_minutes=10080,role="group"),
    ]),True)
    compact,_=build_compact_rows(db,{"antigravity":ag_state})
    ag_row=CompactRow(compact[0],ag_state,now=now)
    columns = ag_row.quota_columns
    assert ag_row.summary_text=="100% · 80%"
    assert [label.text() for label in columns.countdowns] == ["1д 0ч", "6д 23ч"]
    db.close()


def test_monitor_clock_updates_header_countdown_without_rebuilding_cards(tmp_path):
    from claude_usage_manager.quota_monitor_model import MonitorData

    app=qapp();app.setStyleSheet(build_stylesheet()+STYLE);db=Database(tmp_path/"ui.db")
    now=dt.datetime.now(dt.timezone.utc)
    states={"claude":_claude_state(now,now+dt.timedelta(hours=2,minutes=10,seconds=30),now+dt.timedelta(days=2,hours=5,minutes=30))}
    w=MonitorWindow(db,auto_refresh=False);w.timer.stop();w.clock_timer.stop()
    w.states=states;w.rows=build_rows(states);compact,selected=build_compact_rows(db,states)
    w.data=MonitorData(states,w.rows,compact,selected);w._render();w.show()
    w.centralWidget().layout().activate();QTest.qWait(10);app.processEvents()
    header=w.header_rows["claude"]
    assert header.quota_columns.countdowns[0].text() == "2ч 10м" and header.quota_columns.countdowns[1].text() == "2д 5ч"
    row_rect=QRect(header.mapTo(w,QPoint(0,0)),header.size())
    assert row_rect.contains(QRect(header.quota_columns.mapTo(w,QPoint(0,0)),header.quota_columns.size()))
    assert header.quota_columns.sizeHint().width()<=header.quota_columns.width()
    w._refresh_countdowns(now+dt.timedelta(minutes=5))
    assert w.header_rows["claude"] is header
    assert header.quota_columns.countdowns[0].text() == "2ч 5м"
    w.quit_monitor();app.processEvents();db.close()


def _countdown_window(app,db,states):
    '''Окно монитора с заданными состояниями, показано и подогнано, как после обновления данных.'''
    from claude_usage_manager.quota_monitor import FIT_SETTLE_MS
    from claude_usage_manager.quota_monitor_model import MonitorData

    w=MonitorWindow(db,auto_refresh=False);w.timer.stop();w.clock_timer.stop()
    w.states=states;w.rows=build_rows(states);compact,selected=build_compact_rows(db,states)
    w.data=MonitorData(states,w.rows,compact,selected);w._render();w.show()
    # повторный _render у показанного окна - как обычное обновление данных: без запаса высоты под скрытую раскладку
    QTest.qWait(FIT_SETTLE_MS*3);w._render();QTest.qWait(FIT_SETTLE_MS*3);app.processEvents()
    return w


def _four_agents(now,claude_short_reset):
    '''Четыре агента, срок сброса только у пятичасового окна Claude: окно выше минимальной высоты, высоту задают карточки.'''
    return {
        "claude":_claude_state(now,claude_short_reset,None),
        "codex":ProviderState("codex",snap("codex",[QuotaWindow("primary","week",42,window_minutes=10080,role="primary")],"primary"),True),
        "grok":ProviderState("grok",snap("grok",[QuotaWindow("grok:weekly","Grok · общий недельный лимит",93,window_minutes=10080,role="group")],"grok:weekly"),True),
        "antigravity":ProviderState("antigravity",snap("antigravity",[
            QuotaWindow("ag-group:gemini:weekly","Gemini Models",0,window_minutes=10080,role="group"),
            QuotaWindow("ag-group:claude-gpt:weekly","Claude and GPT models",20,window_minutes=10080,role="group"),
        ]),True),
    }


@pytest.mark.parametrize('font_delta', [0, 2])
def test_payments_reset_notice_and_forecast_have_room_in_both_states(tmp_path, font_delta):
    from claude_usage_manager.cache_threads import CacheThread
    from claude_usage_manager.quota_monitor import PAYMENTS_SETTING

    app = qapp()
    db = Database(tmp_path / 'cards.db')
    now = dt.datetime.now(dt.timezone.utc)
    states = _four_agents(now, now + dt.timedelta(hours=4))
    states['claude'] = _claude_state(now, now + dt.timedelta(hours=4), now + dt.timedelta(days=2))
    codex = states['codex'].snapshot
    codex.windows[0].resets_at = now + dt.timedelta(days=4)
    codex.manual_resets = ManualResetCredits(provider='codex', state='available', available_count=2,
        items=[ManualResetItem('credit', 'codex', now + dt.timedelta(days=6))])
    db.set_setting(PAYMENTS_SETTING, json.dumps({p: {'date': '2026-12-01', 'amount': '20 $'} for p in states}))
    db.set_setting('monitor_thread_view', json.dumps({'font': f'+{font_delta}' if font_delta else '0'}))
    db.set_setting('monitor_width', '310')
    w = _countdown_window(app, db, states)
    w.cache_threads['claude'] = [CacheThread('claude', 'test', 'Пример задачи', 'Проект',
                                             'Desktop', now, 3600)]
    original_headers = dict(w.header_rows)
    try:
        assert w.font_delta() == font_delta
        for stage in ('collapsed', 'expanded', 'collapsed-again'):
            if stage != 'collapsed':
                header = w.header_rows['claude']
                QTest.mouseClick(w, Qt.MouseButton.LeftButton, pos=header.mapTo(w, header.rect().center()))
            QTest.qWait(160)
            assert w.header_rows == original_headers
            for header in w.header_rows.values():
                assert header.quota_columns.height() >= header.quota_columns.sizeHint().height()
                for label in header.quota_columns.headlines + header.quota_columns.countdowns:
                    if label.isVisible():
                        assert label.height() >= label.sizeHint().height()
                        assert label.width() >= label.sizeHint().width()
                assert header.payment_label.height() >= header.payment_label.sizeHint().height()
            notice = w.header_rows['codex'].reset_notice
            assert notice.height() >= notice.heightForWidth(notice.width())
            if stage == 'expanded':
                assert w.forecast_bars['claude'].isVisible()
                assert w.cache_blocks['claude'].thread_keys == ['test']
            else:
                assert 'claude' not in w.cache_blocks
            if out := os.environ.get('QUOTA_LAYOUT_CAPTURES'):
                assert w.grab().save(str(pathlib.Path(out) / f'{stage}-{font_delta}.png'))
            initial_width = w.width()
            w._grip_started()
            w._grip_dragged('right', 120)
            app.processEvents()
            assert w.width() > initial_width
            assert notice.height() >= notice.heightForWidth(notice.width())
            w._grip_dragged('right', 0)
            w._grip_finished()
            app.processEvents()
            assert w.width() == initial_width
            assert notice.height() >= notice.heightForWidth(notice.width())
    finally:
        w.quit_monitor()
        app.processEvents()
        db.close()


def test_window_shrinks_back_when_countdown_line_disappears(tmp_path):
    from claude_usage_manager.quota_monitor import FIT_SETTLE_MS

    app=qapp();app.setStyleSheet(build_stylesheet()+STYLE)
    now=dt.datetime.now(dt.timezone.utc)
    db=Database(tmp_path/"plain.db");w=_countdown_window(app,db,_four_agents(now,None))
    plain=w.height();w.quit_monitor();app.processEvents();db.close()
    db=Database(tmp_path/"countdown.db");w=_countdown_window(app,db,_four_agents(now,now+dt.timedelta(minutes=2,seconds=30)))
    header=w.header_rows["claude"]
    assert not header.quota_columns.countdowns[0].isHidden() and w.height()>plain
    # срок прошел на тике часов: строка срока пропала, окно возвращается к высоте без срока, не дожидаясь обновления данных
    w._refresh_countdowns(now+dt.timedelta(minutes=3))
    QTest.qWait(FIT_SETTLE_MS*3);app.processEvents()
    assert w.header_rows["claude"] is header and header.quota_columns.countdowns[0].isHidden()
    assert w.height()==plain
    w.quit_monitor();app.processEvents();db.close()


def test_window_widens_when_countdown_text_grows(tmp_path):
    from claude_usage_manager.quota_monitor import FIT_SETTLE_MS

    app=qapp();app.setStyleSheet(build_stylesheet()+STYLE);db=Database(tmp_path/"ui.db")
    now=dt.datetime.now(dt.timezone.utc);reset=now+dt.timedelta(days=1,seconds=30)
    states={"antigravity":ProviderState("antigravity",snap("antigravity",[
        QuotaWindow("ag-group:gemini:weekly","Gemini Models",0,resets_at=reset,window_minutes=10080,role="group"),
        QuotaWindow("ag-group:claude-gpt:weekly","Claude and GPT models",0,resets_at=reset,window_minutes=10080,role="group"),
    ]),True)}
    w=_countdown_window(app,db,states);label=w.header_rows["antigravity"].quota_columns
    assert label.countdowns[0].text() == "1д 0ч" and label.sizeHint().width()<=label.width()
    # «1д 0ч» -> «23ч 59м»: срок шире процента, окно подгоняется по ширине, текст не обрезается
    w._refresh_countdowns(now+dt.timedelta(minutes=1))
    QTest.qWait(FIT_SETTLE_MS*3);app.processEvents()
    assert label.countdowns[0].text() == "23ч 59м"
    assert label.sizeHint().width()<=label.width()
    w.quit_monitor();app.processEvents();db.close()
