"""PySide6 UI for the compact AI quota monitor."""
from __future__ import annotations

import ctypes
import datetime as dt
import json
import logging
import sys
import traceback

from PySide6.QtCore import QByteArray,QObject,QRunnable,Qt,QThreadPool,QTimer,Signal,Slot
from PySide6.QtGui import QCloseEvent,QIcon
from PySide6.QtWidgets import QApplication,QCheckBox,QComboBox,QDialog,QDialogButtonBox,QFrame,QGridLayout,QHBoxLayout,QLabel,QMainWindow,QProgressBar,QPushButton,QScrollArea,QTreeWidget,QTreeWidgetItem,QVBoxLayout,QWidget

from . import APP_VERSION
from .database import Database
from .icon import ensure_icon
from .provider_settings import load_connections
from .quota_columns import remaining_level
from .models import QuotaSnapshot
from .qt_gui import apply_windows_dark_frame
from .qt_theme import COLORS,build_stylesheet
from .quota_monitor_model import PROVIDERS,PROVIDER_LABELS,LimitCell,MonitorRow,ProviderState,build_rows,cached_states,manual_reset_tooltip,manual_reset_urgency,refresh_states
from .utils import get_logger,now_local,setup_logging

MONITOR_NAME="AI Limits Monitor · Подробно"
MONITOR_VERSION=APP_VERSION
DEFAULT_REFRESH=60
# Собственные ключи окна: общие с виджетом ключи затирали настройки друг друга.
TOPMOST_SETTING="limits_window_always_on_top"
REFRESH_SETTING="limits_window_refresh_seconds"
WIDGET_REFRESH_SETTING="monitor_refresh_seconds"
GAUGE_COLORS={"danger":COLORS["danger"],"warning":COLORS["warning"],"ok":COLORS["success"]}
_MUTEX_NAME="AIUsageMonitor_Details_SingleInstance_v1"
_ALREADY_EXISTS=183


def fmt_reset(value:dt.datetime|None)->str:
    if value is None:return "сброс не указан"
    local=value.astimezone(); today=now_local().date()
    if local.date()==today:return f"сброс {local:%H:%M}"
    if local.date()==today+dt.timedelta(days=1):return f"сброс завтра {local:%H:%M}"
    return f"сброс {local:%d.%m %H:%M}"


def refresh_label(seconds:int)->str:
    return f"{seconds} сек" if seconds<60 else "1 мин" if seconds==60 else f"{seconds//60} мин"


def status_text(state:ProviderState)->str:
    if state.live:return "LIVE"
    if state.snapshot.fallback_source:
        return f"ЛОКАЛЬНЫЕ ДАННЫЕ · {state.snapshot.fallback_source}"
    if state.snapshot.has_data():
        stamp=state.snapshot.updated_at.astimezone().strftime("%d.%m %H:%M") if state.snapshot.updated_at else "?"
        return f"КЭШ · {stamp}"
    return "НЕТ ДАННЫХ"


class Signals(QObject):
    finished=Signal(object); failed=Signal(str)


class Worker(QRunnable):
    def __init__(self,db):super().__init__();self.db=db;self.signals=Signals()
    @Slot()
    def run(self):
        try:self.signals.finished.emit(refresh_states(self.db))
        except Exception:self.signals.failed.emit(traceback.format_exc())


class Gauge(QFrame):
    def __init__(self):
        super().__init__();self.setObjectName("QuotaGauge")
        v=QVBoxLayout(self);v.setContentsMargins(10,7,10,7);v.setSpacing(4)
        h=QHBoxLayout();self.kind=QLabel("—");self.kind.setObjectName("GaugeKind");self.value=QLabel("—");self.value.setObjectName("GaugeValue");h.addWidget(self.kind);h.addStretch();h.addWidget(self.value);v.addLayout(h)
        self.bar=QProgressBar();self.bar.setRange(0,100);self.bar.setTextVisible(False);self.bar.setFixedHeight(4);v.addWidget(self.bar)
        self.reset=QLabel("нет данных");self.reset.setObjectName("GaugeReset");v.addWidget(self.reset)
    def set_cell(self,c:LimitCell|None,stale=False,stale_label="кэш"):
        if c is None or c.remaining is None:
            self.kind.setText("—");self.value.setText("—");self.bar.setValue(0);self.reset.setText("нет окна");return
        x=max(0,min(100,c.remaining));self.kind.setText(c.label);self.value.setText(f"{x:.0f}%");self.bar.setValue(round(x))
        # Пороги общие с виджетом и считаются по показанному (округленному) числу.
        color=GAUGE_COLORS[remaining_level(x)]
        self.bar.setStyleSheet(f"QProgressBar::chunk{{background:{color};border-radius:2px;}}")
        self.reset.setText(fmt_reset(c.resets_at)+(f" · {stale_label}" if stale else ""))


class LimitRowWidget(QFrame):
    def __init__(self,row:MonitorRow,stale=False,stale_label="кэш"):
        super().__init__();self.setObjectName("LimitRow")
        g=QGridLayout(self);g.setContentsMargins(12,8,12,8);g.setHorizontalSpacing(10);g.setColumnStretch(0,4);g.setColumnStretch(1,3);g.setColumnStretch(2,3)
        name=QLabel(row.name);name.setObjectName("MonitorRowTitle");name.setWordWrap(True);sub=QLabel(row.subtitle);sub.setObjectName("MonitorRowSub");g.addWidget(name,0,0);g.addWidget(sub,1,0)
        a=Gauge();a.set_cell(row.short,stale,stale_label);b=Gauge();b.set_cell(row.long,stale,stale_label);g.addWidget(a,0,1,2,1);g.addWidget(b,0,2,2,1)


class ManualResetWidget(QFrame):
    def __init__(self,provider,state):
        super().__init__();self.setObjectName("ManualResetPanel")
        credits=state.snapshot.manual_resets;urgency=manual_reset_urgency(credits);self.setProperty("urgency",urgency);self.setToolTip(manual_reset_tooltip(credits))
        v=QVBoxLayout(self);v.setContentsMargins(10,8,10,8);v.setSpacing(4)
        h=QHBoxLayout();title=QLabel("Кредиты ручного сброса");title.setObjectName("ManualResetTitle");h.addWidget(title);h.addStretch()
        if credits.state=="unknown" or credits.available_count is None:count_text="нет данных"
        elif credits.available_count==0:count_text="0 доступно"
        else:count_text=f"{credits.available_count} доступно"
        count=QLabel(count_text);count.setObjectName("ManualResetCount");count.setProperty("urgency",urgency);h.addWidget(count);v.addLayout(h)
        if credits.state=="unknown":
            text=credits.error or f"Данные {provider} reset credits сейчас недоступны."
            detail=QLabel(text);detail.setObjectName("ManualResetDetail");detail.setWordWrap(True);v.addWidget(detail)
        elif credits.available_count==0:
            detail=QLabel("Доступных кредитов нет");detail.setObjectName("ManualResetDetail");v.addWidget(detail)
        else:
            for index,item in enumerate(credits.sorted_items(),start=1):
                expiry=item.expires_at.astimezone().strftime("%d.%m.%Y %H:%M") if item.expires_at else "срок не указан"
                detail=QLabel(f"Кредит {index} · до {expiry}");detail.setObjectName("ManualResetDetail");v.addWidget(detail)
            missing=max(0,credits.available_count-len(credits.items))
            if missing:
                hint=credits.nearest_expiry_hint.astimezone().strftime("%d.%m.%Y %H:%M") if credits.nearest_expiry_hint else "не указан"
                detail=QLabel(f"Ещё {missing}: сроки источник не предоставил · ближайший {hint}");detail.setObjectName("ManualResetDetail");detail.setWordWrap(True);v.addWidget(detail)
        freshness="кэш" if credits.freshness=="cached" else "свежие данные" if credits.freshness=="live" else "актуальность неизвестна"
        source=QLabel(f"Источник: {credits.source or 'не указан'} · {freshness}");source.setObjectName("ManualResetSource");source.setWordWrap(True);v.addWidget(source)


class ProviderSection(QFrame):
    def __init__(self,provider,state,rows):
        super().__init__();self.setObjectName("ProviderSection")
        v=QVBoxLayout(self);v.setContentsMargins(10,9,10,10);v.setSpacing(7)
        h=QHBoxLayout();title=QLabel(PROVIDER_LABELS[provider]);title.setObjectName("ProviderTitle");h.addWidget(title)
        if state.snapshot.login_method:
            plan=QLabel(state.snapshot.login_method);plan.setObjectName("ProviderPlan");h.addWidget(plan)
        h.addStretch();status=QLabel(status_text(state));status.setObjectName("ProviderLive" if state.live else "ProviderLocal" if state.snapshot.fallback_source else "ProviderStale");h.addWidget(status);v.addLayout(h)
        if rows:
            stale_label = "локальные данные" if state.snapshot.fallback_source else "кэш"
            for row in rows:v.addWidget(LimitRowWidget(row,not state.live,stale_label))
        else:
            msg=QLabel(state.error or "Нет включённых строк");msg.setObjectName("ProviderMessage");msg.setWordWrap(True);v.addWidget(msg)
        if provider in {"claude","codex"} and (state.snapshot.manual_resets.state != "unknown" or state.snapshot.manual_resets.error):v.addWidget(ManualResetWidget(provider,state))


class SettingsDialog(QDialog):
    def __init__(self,rows,visibility,seconds,topmost,parent=None):
        super().__init__(parent);self.setWindowTitle("Настройки AI Limits Monitor");self.resize(560,620);self.visibility=dict(visibility)
        v=QVBoxLayout(self);v.setContentsMargins(16,16,16,16);v.setSpacing(12)
        grid=QGridLayout();self.interval=None
        if seconds is not None:
            # Без своего опроса (окно внутри виджета) интервал задается в настройках виджета.
            grid.addWidget(QLabel("Опрос Codex и Antigravity"),0,0);self.interval=QComboBox()
            for label,val in (("15 секунд",15),("30 секунд",30),("1 минута",60),("2 минуты",120),("5 минут",300)):self.interval.addItem(label,val)
            idx=self.interval.findData(seconds);self.interval.setCurrentIndex(idx if idx>=0 else self.interval.findData(60));grid.addWidget(self.interval,0,1)
        self.topmost=QCheckBox("Это окно поверх остальных окон");self.topmost.setChecked(topmost);grid.addWidget(self.topmost,1,0,1,2);v.addLayout(grid)
        hint=QLabel("Включай и убирай отдельные строки. Новые модели автоматически появятся в списке.");hint.setObjectName("MonitorHint");hint.setWordWrap(True);v.addWidget(hint)
        self.tree=QTreeWidget();self.tree.setObjectName("MonitorSettingsTree");self.tree.setHeaderLabels(["Агент / модель"]);self.tree.setRootIsDecorated(True)
        for provider in PROVIDERS:
            parent_item=QTreeWidgetItem([PROVIDER_LABELS[provider]]);parent_item.setFlags(parent_item.flags()|Qt.ItemFlag.ItemIsUserCheckable);count=0
            for row in rows.get(provider,[]):
                child=QTreeWidgetItem([row.name]);child.setData(0,Qt.ItemDataRole.UserRole,row.key);child.setFlags(child.flags()|Qt.ItemFlag.ItemIsUserCheckable)
                checked=self.visibility.get(row.key,row.default_visible);child.setCheckState(0,Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked);count+=int(checked);parent_item.addChild(child)
            n=parent_item.childCount();parent_item.setCheckState(0,Qt.CheckState.Checked if n and count==n else Qt.CheckState.PartiallyChecked if count else Qt.CheckState.Unchecked);parent_item.setExpanded(True);self.tree.addTopLevelItem(parent_item)
        self.tree.itemChanged.connect(self._changed);v.addWidget(self.tree,1)
        q=QHBoxLayout();allb=QPushButton("Показать всё");allb.clicked.connect(lambda:self._set_all(Qt.CheckState.Checked));noneb=QPushButton("Скрыть всё");noneb.clicked.connect(lambda:self._set_all(Qt.CheckState.Unchecked));q.addWidget(allb);q.addWidget(noneb);q.addStretch();v.addLayout(q)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);v.addWidget(buttons)
    def _changed(self,item,_column):
        if item.parent() is None and item.childCount() and item.checkState(0) in (Qt.CheckState.Checked,Qt.CheckState.Unchecked):
            self.tree.blockSignals(True)
            for i in range(item.childCount()):item.child(i).setCheckState(0,item.checkState(0))
            self.tree.blockSignals(False)
    def _set_all(self,state):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            p=self.tree.topLevelItem(i);p.setCheckState(0,state)
            for j in range(p.childCount()):p.child(j).setCheckState(0,state)
        self.tree.blockSignals(False)
    def result_settings(self):
        mapping={}
        for i in range(self.tree.topLevelItemCount()):
            p=self.tree.topLevelItem(i)
            for j in range(p.childCount()):
                c=p.child(j);key=c.data(0,Qt.ItemDataRole.UserRole)
                if key:mapping[str(key)]=c.checkState(0)==Qt.CheckState.Checked
        return (int(self.interval.currentData()) if self.interval is not None else None),self.topmost.isChecked(),mapping


class MonitorWindow(QMainWindow):
    def __init__(self,db,*,auto_refresh:bool=True,refresh_handler=None):
        # refresh_handler - обновление через виджет и его сборщик; окно тогда само квоты не опрашивает.
        self._refresh_handler=refresh_handler
        super().__init__();self.db=db;self.pool=QThreadPool.globalInstance();self.worker=None;self.states=cached_states(db);self.rows=build_rows(self.states);self.visibility=self._load_visibility();self.seconds=self._load_seconds();self.topmost=db.get_setting(TOPMOST_SETTING,"0")!="0"
        self.setWindowTitle(f"{MONITOR_NAME} {MONITOR_VERSION}");self.setWindowIcon(QIcon(str(ensure_icon())));self.setMinimumSize(650,420);self.resize(760,640);self._build();self._restore_geometry();self._apply_topmost();self._render()
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(self.seconds*1000);
        if auto_refresh:QTimer.singleShot(250,self.refresh)
    def _build(self):
        root=QWidget();root.setObjectName("MonitorRoot");self.setCentralWidget(root);v=QVBoxLayout(root);v.setContentsMargins(14,12,14,12);v.setSpacing(10)
        head=QFrame();head.setObjectName("MonitorHeader");h=QHBoxLayout(head);h.setContentsMargins(12,9,10,9);titles=QVBoxLayout();title=QLabel("AI Limits");title.setObjectName("MonitorTitle");self.subtitle=QLabel();self.subtitle.setObjectName("MonitorSubtitle");titles.addWidget(title);titles.addWidget(self.subtitle);h.addLayout(titles);h.addStretch();self.refresh_btn=QPushButton("Обновить");self.refresh_btn.setObjectName("MonitorSmallButton");self.refresh_btn.clicked.connect(self.refresh);settings=QPushButton("Настройки");settings.setObjectName("MonitorSmallButton");settings.clicked.connect(self.open_settings);h.addWidget(self.refresh_btn);h.addWidget(settings);v.addWidget(head)
        hdr=QGridLayout();hdr.setContentsMargins(20,0,20,0);hdr.setColumnStretch(0,4);hdr.setColumnStretch(1,3);hdr.setColumnStretch(2,3)
        for i,text in enumerate(("Агент / модель","Короткий / день","Долгий / неделя")):
            x=QLabel(text);x.setObjectName("MonitorColumnLabel");hdr.addWidget(x,0,i)
        v.addLayout(hdr)
        self.scroll=QScrollArea();self.scroll.setObjectName("MonitorScroll");self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame);self.content=QWidget();self.content.setObjectName("MonitorContent");self.content_layout=QVBoxLayout(self.content);self.content_layout.setContentsMargins(0,0,0,0);self.content_layout.setSpacing(9);self.content_layout.addStretch();self.scroll.setWidget(self.content);v.addWidget(self.scroll,1)
        foot=QHBoxLayout();self.status=QLabel();self.status.setObjectName("MonitorStatus");foot.addWidget(self.status);foot.addStretch();ver=QLabel(f"v{MONITOR_VERSION}");ver.setObjectName("MonitorVersion");foot.addWidget(ver);v.addLayout(foot)
    def _load_visibility(self):
        try:data=json.loads(self.db.get_setting("monitor_visibility","{}") or "{}");return {str(k):bool(v) for k,v in data.items()} if isinstance(data,dict) else {}
        except json.JSONDecodeError:return {}
    def _load_seconds(self):
        key=WIDGET_REFRESH_SETTING if self._refresh_handler is not None else REFRESH_SETTING
        try:value=int(self.db.get_setting(key,str(DEFAULT_REFRESH)) or DEFAULT_REFRESH)
        except ValueError:value=DEFAULT_REFRESH
        return value if value in {15,30,60,120,300} else DEFAULT_REFRESH
    def _visible(self,row):return self.visibility.get(row.key,row.default_visible)
    def _render(self):
        while self.content_layout.count()>1:
            item=self.content_layout.takeAt(0);w=item.widget()
            if w:w.deleteLater()
        count=0
        try:enabled={key for key,connection in load_connections().items() if connection.enabled}
        except Exception:enabled=set(PROVIDERS)
        if self._refresh_handler is not None:self.seconds=self._load_seconds()
        for provider in PROVIDERS:
            if provider not in enabled:
                # Выключенный источник не показывает пустые шкалы и не входит в счетчики.
                continue
            state=self.states.get(provider) or ProviderState(provider,QuotaSnapshot(provider=provider),False,"Нет данных");rows=[r for r in self.rows.get(provider,[]) if self._visible(r)]
            if rows or state.error:self.content_layout.insertWidget(self.content_layout.count()-1,ProviderSection(provider,state,rows));count+=len(rows)
        if not enabled:
            msg=QLabel("Все источники выключены. Включи нужные в окне подключений виджета.");msg.setObjectName("ProviderMessage");msg.setWordWrap(True);self.content_layout.insertWidget(0,msg)
        states=[s for p,s in self.states.items() if p in enabled]
        stamps=[s.snapshot.updated_at for s in states if s.snapshot.updated_at];latest=max(stamps) if stamps else None;live=sum(s.live for s in states);local=sum((not s.live) and bool(s.snapshot.fallback_source) for s in states);cached=sum((not s.live) and not s.snapshot.fallback_source and s.snapshot.has_data() for s in states)
        text=(f"Обновлено {latest.astimezone():%H:%M:%S}" if latest else "Нет снимков")+f" · live {live}/{len(enabled)} · строк {count}"+(f" · локальные {local}" if local else "")+(f" · кэш {cached}" if cached else "");self.status.setText(text);self.subtitle.setText(f"Остаток лимитов · опрос Codex/Antigravity каждые {refresh_label(self.seconds)}")
    @Slot()
    def refresh(self):
        if self._refresh_handler is not None:
            # Те же данные, что в виджете: запрос идет через его сборщик, окно перерисуется по результату.
            self._refresh_handler();self.status.setText("Обновление запрошено…");return
        if self.worker:return
        self.refresh_btn.setEnabled(False);self.refresh_btn.setText("Обновление…");worker=Worker(self.db);worker.signals.finished.connect(self._done);worker.signals.failed.connect(self._failed);self.worker=worker;self.pool.start(worker)
    @Slot(object)
    def _done(self,states):
        self.worker=None;self.refresh_btn.setEnabled(True);self.refresh_btn.setText("Обновить")
        if isinstance(states,dict):self.states=states;self.rows=build_rows(states)
        self._render()
    @Slot(str)
    def _failed(self,details):self.worker=None;self.refresh_btn.setEnabled(True);self.refresh_btn.setText("Обновить");get_logger().error("AI Limits Monitor refresh: %s",details);self.status.setText("Ошибка обновления · показаны последние данные")
    @Slot()
    def open_settings(self):
        own_polling=self._refresh_handler is None
        d=SettingsDialog(self.rows,self.visibility,self.seconds if own_polling else None,self.topmost,self);d.setStyleSheet(QApplication.instance().styleSheet())
        if d.exec()!=QDialog.DialogCode.Accepted:return
        seconds,self.topmost,self.visibility=d.result_settings()
        if own_polling and seconds is not None:
            self.seconds=seconds;self.db.set_setting(REFRESH_SETTING,str(seconds));self.timer.setInterval(seconds*1000)
        self.db.set_setting(TOPMOST_SETTING,"1" if self.topmost else "0");self.db.set_setting("monitor_visibility",json.dumps(self.visibility,ensure_ascii=False,sort_keys=True));self._apply_topmost();self._render()
    def _apply_topmost(self):
        flags=self.windowFlags();desired=flags|Qt.WindowType.WindowStaysOnTopHint if self.topmost else flags&~Qt.WindowType.WindowStaysOnTopHint
        if desired!=flags:
            visible=self.isVisible();self.setWindowFlags(desired)
            if visible:self.show();apply_windows_dark_frame(self)
    def _restore_geometry(self):
        raw=self.db.get_state("monitor_geometry","") or ""
        if raw:
            try:self.restoreGeometry(QByteArray.fromHex(raw.encode("ascii")))
            except Exception:pass
    def closeEvent(self,event:QCloseEvent):self.db.set_state("monitor_geometry",bytes(self.saveGeometry().toHex()).decode("ascii"));super().closeEvent(event)


STYLE=f'''
QMainWindow,QWidget#MonitorRoot{{background:{COLORS['bg']};}}
QFrame#MonitorHeader{{background:{COLORS['sidebar']};border:1px solid {COLORS['border_soft']};border-radius:11px;}}
QLabel#MonitorTitle{{font-family:"Segoe UI Variable Display";font-size:17pt;font-weight:700;color:{COLORS['text']};}}
QLabel#MonitorSubtitle,QLabel#MonitorStatus,QLabel#MonitorVersion{{color:{COLORS['faint']};font-size:9pt;}}
QPushButton#MonitorSmallButton{{min-height:32px;padding:0 11px;background:{COLORS['panel_alt']};border:1px solid {COLORS['border']};border-radius:7px;}}
QLabel#MonitorColumnLabel{{color:{COLORS['faint']};font-size:8.5pt;font-weight:600;}}
QScrollArea#MonitorScroll,QWidget#MonitorContent{{background:transparent;}}
QFrame#ProviderSection{{background:{COLORS['control']};border:1px solid {COLORS['border_soft']};border-radius:10px;}}
QLabel#ProviderTitle{{font-family:"Segoe UI Variable Display";font-size:12.5pt;font-weight:700;color:{COLORS['text']};}}
QLabel#ProviderPlan{{color:{COLORS['muted']};font-size:8.5pt;padding:2px 7px;background:{COLORS['panel_alt']};border-radius:6px;}}
QLabel#ProviderLive{{color:{COLORS['success']};font-size:8pt;font-weight:700;}} QLabel#ProviderStale{{color:{COLORS['warning']};font-size:8pt;font-weight:700;}}
QLabel#ProviderLocal{{color:{COLORS['accent_hover']};font-size:8pt;font-weight:700;}}
QLabel#ProviderMessage{{color:{COLORS['muted']};font-size:9pt;padding:6px;}}
QFrame#ManualResetPanel{{background:{COLORS['panel_alt']};border:1px solid {COLORS['border_soft']};border-radius:8px;}}
QFrame#ManualResetPanel[urgency="warning"]{{border-color:{COLORS['warning']};}}
QFrame#ManualResetPanel[urgency="critical"]{{border-color:{COLORS['danger']};}}
QLabel#ManualResetTitle{{color:{COLORS['project_text']};font-size:9.5pt;font-weight:650;}}
QLabel#ManualResetCount{{color:{COLORS['text']};font-size:9.5pt;font-weight:700;}}
QLabel#ManualResetCount[urgency="warning"]{{color:{COLORS['warning']};}}
QLabel#ManualResetCount[urgency="critical"]{{color:{COLORS['danger']};}}
QLabel#ManualResetDetail{{color:{COLORS['muted']};font-size:8.5pt;}}
QLabel#ManualResetSource{{color:{COLORS['faint']};font-size:7.8pt;}}
QFrame#LimitRow{{background:{COLORS['table']};border:1px solid {COLORS['border_soft']};border-radius:8px;}}
QLabel#MonitorRowTitle{{color:{COLORS['project_text']};font-size:10.5pt;font-weight:650;}} QLabel#MonitorRowSub{{color:{COLORS['faint']};font-size:8.5pt;}}
QFrame#QuotaGauge{{background:{COLORS['panel_alt']};border:1px solid {COLORS['border_soft']};border-radius:7px;}}
QLabel#GaugeKind{{color:{COLORS['muted']};font-size:8pt;}} QLabel#GaugeValue{{color:{COLORS['text']};font-family:"Segoe UI Variable Display";font-size:13pt;font-weight:700;}} QLabel#GaugeReset{{color:{COLORS['faint']};font-size:7.8pt;}}
QTreeWidget#MonitorSettingsTree{{font-size:10pt;}} QLabel#MonitorHint{{color:{COLORS['muted']};font-size:9pt;}}
'''


def _mutex():
    if sys.platform!="win32":return True
    k=ctypes.windll.kernel32;h=k.CreateMutexW(None,False,_MUTEX_NAME)
    if k.GetLastError()==_ALREADY_EXISTS:
        if h:k.CloseHandle(h)
        return None
    return h


def run_monitor():
    setup_logging(logging.INFO);m=_mutex()
    if m is None:return 0
    db=Database();app=QApplication.instance() or QApplication(sys.argv);app.setApplicationName(MONITOR_NAME);app.setApplicationVersion(MONITOR_VERSION);app.setStyle("Fusion");app.setStyleSheet(build_stylesheet()+STYLE);w=MonitorWindow(db);w.show();apply_windows_dark_frame(w);w.raise_();w.activateWindow()
    try:return app.exec()
    finally:
        db.close()
        if sys.platform=="win32" and m not in (None,True):ctypes.windll.kernel32.CloseHandle(m)


def main():
    try:return run_monitor()
    except Exception:get_logger().error("AI Limits Monitor fatal: %s",traceback.format_exc());return 1
