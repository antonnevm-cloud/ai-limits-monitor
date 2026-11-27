"""Compact always-on-top PySide6 widget for AI quota monitoring."""
from __future__ import annotations
import ctypes, datetime as dt, json, logging, re, sys, time, traceback
from html import escape
from pathlib import Path
from PySide6.QtCore import QByteArray,QObject,QPoint,QRect,QRectF,QRunnable,Qt,QThreadPool,QTimer,Signal,Slot
from PySide6.QtGui import QCloseEvent,QColor,QFont,QIcon,QPainter,QPen,QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication,QBoxLayout,QCheckBox,QComboBox,QDialog,QDialogButtonBox,QFrame,QGridLayout,QHBoxLayout,QLabel,QLayout,QLineEdit,QMainWindow,QMenu,QMessageBox,QPushButton,QSizePolicy,QSlider,QSystemTrayIcon,QToolButton,QVBoxLayout,QWidget
from .database import Database
from .subscription_payments import next_monthly_payment, parse_last_payment
from .icon import ensure_icon
from .models import QuotaSnapshot
from . import APP_VERSION
from .provider_settings import load_connections, provider_enabled
from .cache_threads import CODEX_TTL_DEFAULT,GROK_TTL_DEFAULT,ThreadScanner,is_claude_terminal,select_threads
from .cache_threads_qt import AGENT_NAMES, MIN_VISIBLE_ROWS, ROW_HEIGHT, CacheThreadsBlock, ElidedLabel, LinkButton, distribute_height_caps
from .forecast_qt import ForecastBar, ForecastPopup
from .thread_activity import ACTIVITY_WORKING
from .thread_usage import EMPTY_REPORT, UsageScanner
from .week_forecast import WEEK, build_chart, build_forecast, weekly_window
from .edge_grip import GRIP_WIDTH, EdgeGrip
from .thread_view import FIELDS as THREAD_VIEW_FIELDS, SETTING_KEY as THREAD_VIEW_KEY, ThreadView
from .toggle_switch import ToggleSwitch
from .claude_local import bridge_command, install_statusline_command, manual_resets_edit_text, save_manual_resets
from .claude_local import claude_root, read_statusline_quota, statusline_path
from .claude_reset_dialog import ClaudeManualResetsDialog
from .quota_columns import QuotaColumns, cell_label, limit_suffix, manual_reset_suffix, percent_text, primary_cell
from .qt_theme import COLORS,build_stylesheet
from .quota_monitor_model import PROVIDERS,PROVIDER_LABELS,MonitorData,ProviderState,build_compact_rows,build_rows,cached_states,manual_reset_tooltip,refresh_monitor_data
from .utils import get_logger,now_local,setup_logging
from .windows_ui import enable_per_monitor_dpi_awareness,set_window_always_on_top

MONITOR_NAME='AI Limits Monitor'; MONITOR_VERSION=APP_VERSION; DEFAULT_REFRESH=60; CACHE_SCAN_MS=10000; USAGE_SCAN_MS=30000; FORECAST_REFRESH_TICKS=30; FORECAST_SAMPLES_TTL=60; DEFAULT_LIST_WIDTH=380; CACHE_PROVIDERS=('claude','codex','grok'); CACHE_TTL_CHOICES={300,600,900,1800,3600}
_MUTEX_NAME='AIUsageMonitor_SingleInstance_v1'; _ALREADY_EXISTS=183
BADGES={'claude':'C','codex':'X','grok':'G','antigravity':'A'}
BADGE_COLORS={'claude':'#A86F58','codex':'#426D72','grok':'#6A6F76','antigravity':'#5D4A87'}
COMPACT_WIDTH=180; COMPACT_MIN_WIDTH=180; COMPACT_MIN_HEIGHT=150; FIT_SETTLE_MS=60; CACHE_SCREEN_MARGIN=24; MAX_USER_WIDTH=3000; DRAWER_WIDTH=300
ICON_LOGICAL_SIZE=18; ICON_BOX_SIZE=18; RESET_NOTICE_DAYS=14; RESET_CRITICAL_DAYS=3
PAYMENTS_SETTING = 'monitor_subscription_payments'
PAYMENT_AMOUNT_LENGTH = 24
Payment = tuple[dt.date, str]

def fmt_reset(value):
    if value is None:return ''
    local=value.astimezone(); today=now_local().date()
    if local.date()==today:return f'сброс {local:%H:%M}'
    if local.date()==today+dt.timedelta(days=1):return f'сброс завтра {local:%H:%M}'
    return f'сброс {local:%d.%m %H:%M}'

def refresh_label(seconds): return f'{seconds} сек' if seconds<60 else '1 мин' if seconds==60 else f'{seconds//60} мин'
def percent_with_manual_resets(value,credits=None):
    return percent_text(value)+manual_reset_suffix(credits)
def state_status(state):
    if state is None or not state.snapshot.has_data(): return 'нет данных'
    if state.live: return 'онлайн'
    if state.snapshot.fallback_source: return 'локальные данные'
    return 'кэш'

SOURCE_NOTES={'codex':'Лимиты сообщает установленный клиент Codex.','grok':'Лимиты сообщает установленный клиент Grok Build.','antigravity':'Лимиты сообщает установленный клиент Antigravity.'}
def dot_tooltip(provider,status,state):
    """Подсказка цветной точки: что значит цвет именно для этого агента и откуда данные."""
    name=PROVIDER_LABELS.get(provider,provider);stamp=state.snapshot.updated_at;when=stamp.astimezone().strftime('%d.%m %H:%M') if stamp else 'неизвестно';note=(state.error or '').strip();source=SOURCE_NOTES.get(provider,'')
    if status=='онлайн':lines=[f'ОНЛАЙН — {name}: данные получены от источника.',source,f'{note} Данные от {when}.' if note else '']
    elif status=='локальные данные':
        if provider=='claude':return 'ЛОКАЛЬНЫЕ ДАННЫЕ — основной источник недоступен; показана локальная история Claude Code.'
        lines=[f'ЛОКАЛЬНЫЕ ДАННЫЕ — {name}: основной источник недоступен. {state.snapshot.fallback_source}.',source]
    elif status=='кэш':lines=[f'КЭШ — {name}: источник сейчас не отвечает, показан последний сохранённый снимок от {when}.',f'Причина: {note}' if note else 'Причина записана в backend.log.',source]
    else:lines=[f'НЕТ ДАННЫХ — {name}: нет ни текущего ответа, ни сохранённого снимка.',f'Причина: {note}' if note else '']
    return '\n'.join(line for line in lines if line)

class WorkerSignals(QObject):
    finished=Signal(object); failed=Signal(str)
class RefreshWorker(QRunnable):
    def __init__(self,db):super().__init__();self.db=db;self.signals=WorkerSignals()
    @Slot()
    def run(self):
        try:
            data=refresh_monitor_data(self.db)
        except Exception:self.signals.failed.emit(traceback.format_exc());return
        self.signals.finished.emit(data)

class CacheScanWorker(QRunnable):
    """Читает журналы Claude и Codex в фоне: только чтение, без сети."""
    def __init__(self,scanner):super().__init__();self.scanner=scanner;self.signals=WorkerSignals()
    @Slot()
    def run(self):
        try:
            now=dt.datetime.now(dt.timezone.utc);found=self.scanner.scan(now);shown={provider:select_threads(threads,now)[0] for provider,threads in found.items()}
        except Exception:self.signals.failed.emit(traceback.format_exc());return
        self.signals.finished.emit(shown)

PROVIDER_ASSET_DIR=Path(__file__).resolve().parents[1]/'assets'/'providers'

class UsageScanWorker(QRunnable):
    '''Считает расход тредов за неделю лимита по журналам Claude и Codex в фоне: только чтение, без сети.'''
    def __init__(self,scanner,starts,used):super().__init__();self.scanner=scanner;self.starts=starts;self.used=used;self.signals=WorkerSignals()
    @Slot()
    def run(self):
        try:report=self.scanner.scan(self.starts,self.used)
        except Exception:self.signals.failed.emit(traceback.format_exc());return
        self.signals.finished.emit(report)

def short_reset(value):
    if value is None:return ''
    return value.astimezone().strftime('%d.%m %H:%M')

def normalize_cached_reset(value,provider='',live=True,updated_at=None,now=None):
    """Keep cached Antigravity reset timestamps factual.

    A cached timestamp is historical evidence, not a license to invent the
    next reset from a model-specific cadence.  Grouped live data now supplies
    the authoritative weekly reset; when it is unavailable the old timestamp
    remains visible alongside the `КЭШ` indicator.
    """
    return value

def display_reset(value,provider='',live=True,updated_at=None,now=None):
    return short_reset(normalize_cached_reset(value,provider,live,updated_at,now))

def agent_summary(row,state):
    '''Текст справа в заголовке карточки агента («5 ч 62% · нед 30%») и подсказка с остатками, сроками сброса и кредитами ручного сброса.'''
    credits = state.snapshot.manual_resets
    parts, lines = [], []
    for cell in (row.short,row.long):
        if cell is None:continue
        suffix = limit_suffix(row, cell, credits)
        label=cell_label(cell);parts.append(f'{label} {percent_text(cell.remaining)}{suffix}'.strip())
        reset=fmt_reset(cell.resets_at);lines.append(f'{cell.label}: осталось {percent_text(cell.remaining)}'+(f', {reset}' if reset else ''))
    if credits and credits.state == 'available' and manual_reset_suffix(credits):
        lines.append(manual_reset_tooltip(credits))
    return (' · '.join(parts) or '—'),'\n'.join(lines)

def expiring_reset(credits,now=None,days=RESET_NOTICE_DAYS):
    '''Ближайший срок доступного полного сброса, если до него меньше `days` суток; иначе None.'''
    if credits is None or credits.state!='available' or not credits.available_count:return None
    expiry=credits.nearest_expiry()
    if expiry is None:return None
    now=now or dt.datetime.now(dt.timezone.utc);left=expiry.astimezone(dt.timezone.utc)-now.astimezone(dt.timezone.utc)
    return expiry if dt.timedelta(0)<left<dt.timedelta(days=days) else None

def reset_notice_text(credits,expiry,now=None):
    now=now or dt.datetime.now(dt.timezone.utc);left=expiry.astimezone(dt.timezone.utc)-now.astimezone(dt.timezone.utc);local=expiry.astimezone()
    when=f'через {left.days} дн.' if left.days>=1 else f'через {max(1,round(left.total_seconds()/3600))} ч'
    count=credits.available_count or 1
    if count==1:return f'Есть полный сброс: сгорит {local:%d.%m} {local:%H:%M} ({when}), успей использовать'
    return f'Полных сбросов: {count}, ближайший сгорит {local:%d.%m} {local:%H:%M} ({when}), успей использовать'

class ProviderIcon(QLabel):
    '''Маленький одноцветный значок агента в заголовке карточки.'''
    def __init__(self,provider,parent=None):
        super().__init__(parent);self.provider=provider;self.setFixedSize(ICON_BOX_SIZE,ICON_BOX_SIZE);self.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed);self.setAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter);self._set_provider_pixmap()
    def _device_pixel_ratio(self):
        screen=self.screen() or QApplication.primaryScreen()
        return max(1.0,float(screen.devicePixelRatio() if screen else self.devicePixelRatioF()))
    def _asset_path(self):
        for suffix in ('.svg','.png','.ico'):
            path=PROVIDER_ASSET_DIR/f'{self.provider}{suffix}'
            if path.exists():return path
        return None
    def _set_provider_pixmap(self):
        path=self._asset_path();dpr=self._device_pixel_ratio();physical_size=max(1,round(ICON_LOGICAL_SIZE*dpr));pix=QPixmap()
        if path and path.suffix.casefold()=='.svg':
            renderer=QSvgRenderer(str(path))
            if renderer.isValid():
                pix=QPixmap(physical_size,physical_size);pix.fill(Qt.GlobalColor.transparent);painter=QPainter(pix);renderer.render(painter,QRectF(0,0,physical_size,physical_size));painter.end();pix.setDevicePixelRatio(dpr)
        elif path:
            pix=QPixmap(str(path))
            if not pix.isNull() and pix.width()>=physical_size and pix.height()>=physical_size:pix=pix.scaled(physical_size,physical_size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation);pix.setDevicePixelRatio(dpr)
        if not pix.isNull():
            mono=QPixmap(pix.size());mono.setDevicePixelRatio(dpr);mono.fill(Qt.GlobalColor.transparent);painter=QPainter(mono);painter.drawPixmap(0,0,pix);painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn);painter.fillRect(QRectF(0,0,ICON_LOGICAL_SIZE,ICON_LOGICAL_SIZE),QColor('#F0F3F7'));painter.end();self.setPixmap(mono)
        else:self.setText('•')

class DisclosureButton(QToolButton):
    SIDE = 26

    def __init__(self, agent_name):
        super().__init__()
        self.agent_name = agent_name
        self.setObjectName('CardDisclosure')
        self.setCheckable(True)
        self.setFixedSize(self.SIDE, self.SIDE)
        policy = self.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.setSizePolicy(policy)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_expanded(False)

    def set_expanded(self, expanded):
        arrow = Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        if self.isChecked() == expanded and self.arrowType() == arrow:
            return
        self.setChecked(expanded)
        self.setArrowType(arrow)
        verb = 'Свернуть' if expanded else 'Развернуть'
        label = f'{verb} треды {self.agent_name}'
        self.setAccessibleName(label)
        self.setToolTip(label)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class CompactRow(QFrame):
    '''Заголовок карточки агента по макету: имя слева, справа остатки лимитов и точка состояния данных.'''
    def __init__(self,row,state,parent=None,running=None,font_delta=0.0,now=None,*,payment=None):
        super().__init__(parent);self.row=row;self.state=state;self.font_delta=font_delta;self.setObjectName('CompactRow');self.setMouseTracking(True);self.agent_name=AGENT_NAMES.get(row.provider,row.name);self.running=None
        outer = QVBoxLayout(self)
        self._outer = outer
        outer.setContentsMargins(6, 7, 6, 7)
        outer.setSpacing(4)
        self._heading = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._heading.setSpacing(4)
        outer.addLayout(self._heading)
        self._identity = QWidget()
        h = QHBoxLayout(self._identity)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self.disclosure = None
        if row.provider in CACHE_PROVIDERS:
            self.disclosure = DisclosureButton(self.agent_name)
            h.addWidget(self.disclosure, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            spacer = QWidget()
            spacer.setFixedSize(DisclosureButton.SIDE, 1)
            spacer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            h.addWidget(spacer)
        self.icon = ProviderIcon(row.provider)
        h.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        identity = QVBoxLayout()
        identity.setSpacing(0)
        identity.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.name_label = QLabel(self.agent_name)
        self.name_label.setObjectName('AgentName')
        title = QHBoxLayout()
        title.setSpacing(3)
        title.addWidget(self.name_label)
        self.dot = QLabel('●')
        self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.addWidget(self.dot)
        title.addStretch()
        identity.addLayout(title)
        self.payment_label = ElidedLabel()
        self.payment_label.setObjectName('AgentPayment')
        self.payment_label.setTextFormat(Qt.TextFormat.PlainText)
        self.payment_label.hide()
        identity.addWidget(self.payment_label)
        h.addLayout(identity, 1)
        self.payment = None
        self._payment_as_of = None
        self.quota_columns = QuotaColumns(font_delta)
        self.quota_columns.layout().setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self._heading.addWidget(self._identity, 1)
        self._heading.addWidget(self.quota_columns, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.reset_notice = None
        self.set_running(running)
        self.set_data(row,state,running,now)
        self.set_payment(payment)

    def fit_width(self, card_width):
        self._identity.layout().activate()
        self.quota_columns.layout().activate()
        margins = self._outer.contentsMargins()
        width = (margins.left() + self._identity.layout().minimumSize().width() + self._heading.spacing()
                 + self.quota_columns.sizeHint().width() + margins.right())
        side_by_side = width <= card_width
        direction = QBoxLayout.Direction.LeftToRight if side_by_side else QBoxLayout.Direction.TopToBottom
        if self._heading.direction() != direction:
            self._heading.setDirection(direction)
            self._heading.setStretch(0, int(side_by_side))
        self._outer.invalidate()
        self._outer.activate()
        self.updateGeometry()

    def set_payment(self, payment: Payment | None, today: dt.date | None = None) -> bool:
        today = today or now_local().date()
        if self.payment == payment and (payment is None or today == self._payment_as_of):
            return False
        self.payment = payment
        self._payment_as_of = today
        if payment is None:
            self.payment_label.hide()
        else:
            last_paid, amount = payment
            date = next_monthly_payment(last_paid.day, today)
            text = f'{date:%d.%m} · {amount}'
            self.payment_label.ensurePolished()
            metrics = self.payment_label.fontMetrics()
            width = min(metrics.horizontalAdvance(text), metrics.horizontalAdvance('00.00 · 00 000 ₽'))
            self.payment_label.setMinimumWidth(width)
            self.payment_label.set_full_text(text)
            self.payment_label.setToolTip(f'<qt>Следующий платеж {date:%d.%m.%Y} · {escape(amount)}</qt>')
            self.payment_label.show()
        return True

    def set_data(self,row,state,running=None,now=None):
        """Обновить значения внутри существующей карточки."""
        self.row=row;self.state=state
        self.summary_text, tip = agent_summary(row, state)
        changed = self.quota_columns.set_values(row, state, now)
        self.quota_columns.setToolTip(tip)
        if self.running != running:
            self.set_running(running)
            changed = True
        status=state_status(state);name={'онлайн':'OnlineDot','локальные данные':'LocalDot','кэш':'CacheDot','нет данных':'OfflineDot'}[status]
        if self.dot.objectName()!=name:
            self.dot.setObjectName(name);self.dot.style().unpolish(self.dot);self.dot.style().polish(self.dot)
        dot_tip=dot_tooltip(row.provider,status,state)
        if row.provider=='claude':
            stamp=state.snapshot.updated_at
            when=stamp.astimezone().strftime('%d.%m %H:%M:%S') if stamp else 'неизвестно'
            if status=='нет данных':dot_tip='Claude: свежих локальных квот нет. Ожидается событие Claude Code или обновление истории Claude Desktop.'
            elif state.snapshot.source=='claude-desktop:local-history':dot_tip=f'Claude Desktop · локальная статистика. Последнее измерение: {when}.\nПроценты получены из локальной истории без запросов к Anthropic. Точные сроки сброса в истории отсутствуют.'
            else:dot_tip+=f'\nПоследнее измерение: {when}. Новые данные появляются после обновления источника Claude.'
        self.dot.setToolTip(dot_tip)
        credits=state.snapshot.manual_resets;expiry=expiring_reset(credits,now)
        if expiry is None and self.reset_notice is not None:
            self._outer.removeWidget(self.reset_notice);self.reset_notice.hide();self.reset_notice.deleteLater();self.reset_notice=None;changed=True
        elif expiry is not None:
            if self.reset_notice is None:
                self.reset_notice=QLabel();self.reset_notice.setObjectName('ResetNotice');self.reset_notice.setWordWrap(True);self._outer.addWidget(self.reset_notice);changed=True
            notice=reset_notice_text(credits,expiry,now)
            if self.reset_notice.text()!=notice:self.reset_notice.setText(notice);changed=True
            critical=expiry.astimezone(dt.timezone.utc)-(now or dt.datetime.now(dt.timezone.utc))<dt.timedelta(days=RESET_CRITICAL_DAYS)
            if self.reset_notice.property('critical')!=critical:
                self.reset_notice.setProperty('critical',critical);self.reset_notice.style().unpolish(self.reset_notice);self.reset_notice.style().polish(self.reset_notice)
            self.reset_notice.setToolTip(manual_reset_tooltip(credits,now=now))
        return changed
    def refresh_countdown(self,now=None):
        '''Пересчитывает срок до сброса под процентами; текст меняется только при смене минуты, карточка не пересобирается. True - текст изменился: могла смениться и высота (строка срока появилась или пропала), и ширина («1д 0ч» -> «23ч 59м»), окну нужна подгонка.'''
        return self.quota_columns.set_countdowns(self.row, now)
    def set_running(self,count):
        '''Число работающих сейчас тредов в скобках после имени агента; None - данных нет, скобок нет.'''
        self.running=count;self.name_label.setText(self.agent_name if count is None else f'{self.agent_name} ({count})')
        self.name_label.setToolTip('' if count is None else f'Работает тредов сейчас: {count}')

class ShellFrame(QFrame):
    '''Подложка окна: скругленный темный фон, всегда плотный. Под полупрозрачным фоном Qt сглаживает текст серым (ClearType только над непрозрачными пикселями), и текст мылится; прозрачность из настройки задается всему окну (setWindowOpacity). Рисуется вручную: у прозрачного окна фон из стиля не рисуется.'''
    COLOR=QColor(9,12,16)
    def paintEvent(self,event):
        # рамка в целое число физических пикселей: при масштабе 150% линия в 1 логический пиксель размывается на два
        dpr=max(1.0,self.devicePixelRatioF());width=max(1,round(dpr))/dpr;inset=width/2
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setPen(QPen(QColor(COLORS['border']),width));painter.setBrush(self.COLOR);painter.drawRoundedRect(QRectF(self.rect()).adjusted(inset,inset,-inset,-inset),14.0,14.0)

class AgentCard(QFrame):
    '''Карточка агента: заголовок, прогноз недели и список тредов в одной рамке.'''
    def __init__(self, header, parent=None):
        super().__init__(parent)
        self.setObjectName('AgentCard')
        self.header = header
        self.forecast = None
        self.block = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(header)

    def set_details(self, forecast, block):
        if self.forecast is forecast and self.block is block:
            return False
        box = self.layout()
        for old in (self.forecast, self.block):
            if old is not None and old is not forecast and old is not block:
                box.removeWidget(old)
                old.hide()
                old.deleteLater()
        if forecast is not None and forecast is not self.forecast:
            box.insertWidget(1, forecast)
        if block is not None and block is not self.block:
            box.addWidget(block)
        self.forecast, self.block = forecast, block
        self.header.setProperty('divider', block is not None)
        self.header.style().unpolish(self.header)
        self.header.style().polish(self.header)
        if forecast is not None:
            forecast.setProperty('divider', block is not None)
        return True


class SettingsDialog(QDialog):
    connections_requested = Signal()
    def __init__(self,data,visibility,seconds,topmost,opacity,ag_mode,fixed_id,parent=None,*,payments=None):
        super().__init__(parent);self.setWindowTitle('Настройки AI Limits Monitor');self.resize(520,470);v=QVBoxLayout(self);v.setContentsMargins(16,16,16,16);v.setSpacing(10);grid=QGridLayout();grid.addWidget(QLabel('Опрос Codex и Antigravity'),0,0);self.interval=QComboBox()
        for text,val in (('15 секунд',15),('30 секунд',30),('1 минута',60),('2 минуты',120),('5 минут',300)):self.interval.addItem(text,val)
        self.interval.setCurrentIndex(max(0,self.interval.findData(seconds)));grid.addWidget(self.interval,0,1);grid.addWidget(QLabel('Прозрачность окна'),1,0);self.opacity=QComboBox()
        for val in (20,30,40,50,60,70,80,90,100):self.opacity.addItem(f'{val}%',val)
        self.opacity.setCurrentIndex(max(0,self.opacity.findData(opacity)));grid.addWidget(self.opacity,1,1);self.topmost=QCheckBox('Поверх остальных окон');self.topmost.setChecked(topmost);grid.addWidget(self.topmost,2,0,1,2);v.addLayout(grid);v.addWidget(QLabel('Показывать'));ph=QHBoxLayout();self.provider_checks={}
        for provider in PROVIDERS:
            cb=QCheckBox(PROVIDER_LABELS[provider]);cb.setChecked(visibility.get(f'compact:{provider}',True));self.provider_checks[provider]=cb;ph.addWidget(cb)
        v.addLayout(ph)
        connections = QPushButton("Подключения и вход")
        connections.clicked.connect(self.connections_requested)
        v.addWidget(connections)
        group_rows=[row for row in data.rows.get('antigravity',[]) if any(cell and cell.window_id.startswith('ag-group:') for cell in (row.short,row.long))]
        grouped=bool(group_rows)
        v.addWidget(QLabel('Antigravity · недельные группы' if grouped else 'Antigravity · модель по умолчанию'));self.ag_mode=QComboBox()
        options=(('Обе группы в compact','auto_today'),) if grouped else (('Авто: самая используемая сегодня','auto_today'),('Авто: последняя использованная','last_used'),('Фиксированная модель','fixed'),('Самый израсходованный лимит','most_consumed'))
        for text,val in options:self.ag_mode.addItem(text,val)
        idx=self.ag_mode.findData(ag_mode);self.ag_mode.setCurrentIndex(idx if idx>=0 else 0);v.addWidget(self.ag_mode);self.fixed=QComboBox();self.fixed.addItem('Выберите группу' if grouped else 'Выберите модель','')
        for row in (group_rows or data.rows.get('antigravity',[])):
            selection=next((cell for cell in (row.long,row.short) if cell and cell.window_id.startswith('ag-group:')),row.short or row.long)
            self.fixed.addItem(row.name,selection.window_id if selection else '')
        idx=self.fixed.findData(fixed_id);self.fixed.setCurrentIndex(idx if idx>=0 else 0);v.addWidget(self.fixed)
        if grouped:
            self.ag_mode.setEnabled(False);self.fixed.setEnabled(False)
        hint=QLabel('В compact-режиме одновременно показываются обе недельные группы: Gemini Models и Claude/GPT Models.' if grouped else 'Для legacy/model-only источника показывается один фактический лимит модели.');hint.setWordWrap(True);hint.setObjectName('SettingsHint');v.addWidget(hint)

        self.payments = dict(payments or {})
        self.payment_fields = {}
        v.addWidget(QLabel('Платежи по подпискам'))
        payment_grid = QGridLayout()
        payment_grid.setVerticalSpacing(5)
        payment_grid.addWidget(QLabel('Последняя оплата'), 0, 1)
        payment_grid.addWidget(QLabel('Сумма'), 0, 2)
        for row, provider in enumerate(PROVIDERS, start=1):
            name = PROVIDER_LABELS[provider]
            date_field = QLineEdit(placeholderText='ДД.ММ', maxLength=5)
            amount_field = QLineEdit(placeholderText='20 $', maxLength=PAYMENT_AMOUNT_LENGTH)
            date_field.setObjectName('PaymentInput')
            amount_field.setObjectName('PaymentInput')
            date_field.setAccessibleName(f'{name}: последняя оплата')
            amount_field.setAccessibleName(f'{name}: сумма платежа')
            if provider in self.payments:
                date, amount = self.payments[provider]
                date_field.setText(f'{date:%d.%m}')
                amount_field.setText(amount)
            payment_grid.addWidget(QLabel(name), row, 0)
            payment_grid.addWidget(date_field, row, 1)
            payment_grid.addWidget(amount_field, row, 2)
            self.payment_fields[provider] = date_field, amount_field
        v.addLayout(payment_grid)
        v.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Сохранить')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def accept(self):
        payments = {}
        today = now_local().date()
        for provider, (date_field, amount_field) in self.payment_fields.items():
            date_text = date_field.text().strip()
            amount = amount_field.text().strip()
            if not date_text and not amount:
                continue
            try:
                previous = self.payments.get(provider)
                if previous and date_text == previous[0].strftime('%d.%m'):
                    date = previous[0]
                else:
                    date = parse_last_payment(date_text, today)
            except ValueError:
                QMessageBox.warning(self, 'Платеж по подписке', f'{PROVIDER_LABELS[provider]}. Укажи число и месяц последней оплаты в формате ДД.ММ.')
                date_field.setFocus()
                return
            if not amount:
                QMessageBox.warning(self, 'Платеж по подписке', f'{PROVIDER_LABELS[provider]}. Укажи сумму платежа.')
                amount_field.setFocus()
                return
            payments[provider] = date, amount
        self.payments = payments
        super().accept()

    def values(self):
        vis={f'compact:{p}':cb.isChecked() for p,cb in self.provider_checks.items()};return int(self.interval.currentData()),self.topmost.isChecked(),int(self.opacity.currentData()),vis,str(self.ag_mode.currentData()),str(self.fixed.currentData() or '')

class MonitorWindow(QMainWindow):
    def __init__(self,db,*,auto_refresh=True,cache_scan=None):
        super().__init__();self.db=db;self.pool=QThreadPool.globalInstance();self.worker=None;self._drag=None;self._quit=False;self._auto_refresh=auto_refresh;self.states=cached_states(db);self.rows=build_rows(self.states);compact,selected=build_compact_rows(db,self.states);self.data=MonitorData(self.states,self.rows,compact,selected);self.seconds=self._load_int('monitor_refresh_seconds',DEFAULT_REFRESH,{15,30,60,120,300});self.opacity=self._load_int('monitor_opacity',90,{20,30,40,50,60,70,80,90,100});self.topmost=db.get_setting('monitor_always_on_top','1')!='0';self.visibility=self._load_json('monitor_compact_visibility');self.user_width=self._load_width();self._resize_origin=None;self.grips={};self.thread_view=ThreadView.from_json(db.get_setting(THREAD_VIEW_KEY));self.thread_rules=db.thread_rules();self.view_switches={};self._init_cache_threads(auto_refresh if cache_scan is None else cache_scan)
        self.payments = self._load_payments()
        self.setWindowTitle(f'{MONITOR_NAME} {MONITOR_VERSION}');self.setWindowIcon(QIcon(str(ensure_icon())));self._enable_transparency(self);self.setMinimumSize(COMPACT_MIN_WIDTH,COMPACT_MIN_HEIGHT);self.resize(COMPACT_WIDTH,COMPACT_MIN_HEIGHT);self._build();self._apply_font();self._restore_geometry();self._apply_window_flags();self._apply_opacity();self._render();self._build_tray();self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(self.seconds*1000);self.clock_timer=QTimer(self);self.clock_timer.timeout.connect(self._update_clock);self.clock_timer.start(1000);self._update_clock()
        if auto_refresh:
            QTimer.singleShot(250,self.refresh)
            QTimer.singleShot(5000,self.refresh)
    def _build(self):
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        shell = ShellFrame()
        shell.setObjectName('WidgetShell')
        self._enable_transparency(shell)
        self.setCentralWidget(shell)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(GRIP_WIDTH + 1, 6, GRIP_WIDTH + 1, 7)
        layout.setSpacing(3)

        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 0, 0)
        title = QLabel('AI Limits')
        title.setObjectName('WidgetTitle')
        title.setToolTip('Потяни за заголовок, чтобы переместить виджет')
        header.addWidget(title)
        header.addStretch()
        self.updated = QLabel()
        self.updated.setObjectName('UpdatedLabel')
        header.addWidget(self.updated)
        self.hide_button = self._command_button('Скрыть', 'Скрыть виджет в области уведомлений')
        self.hide_button.setObjectName('HideWidget')
        self.hide_button.clicked.connect(self.hide_to_tray)
        header.addWidget(self.hide_button)
        layout.addLayout(header)

        self.actions = QWidget()
        commands = QHBoxLayout(self.actions)
        commands.setContentsMargins(0, 0, 0, 0)
        commands.setSpacing(2)
        self.refresh_button = self._command_button('Обновить', 'Обновить данные о лимитах')
        self.refresh_button.clicked.connect(self.refresh_now)
        self.analytics_button = self._command_button('Аналитика', 'Открыть подробную аналитику расхода')
        self.analytics_button.clicked.connect(self.open_detail)
        self.menu_button = self._command_button('Меню', 'Настройки вида, подключения и другие команды')
        for button in (self.refresh_button, self.analytics_button, self.menu_button):
            commands.addWidget(button, 1)
        layout.addWidget(self.actions)

        self.drawer = self._make_drawer()
        self.rows_box = QVBoxLayout()
        self.rows_box.setContentsMargins(0, 0, 0, 0)
        self.rows_box.setSpacing(2)
        layout.addLayout(self.rows_box)
        self.footer = QLabel()
        self.footer.setObjectName('WidgetFooter')
        self.footer.setWordWrap(True)
        self.footer.setToolTip('Онлайн: свежие данные. Локальные: данные из истории. Кэш: последний сохраненный снимок. Нет данных: источник и снимок недоступны.')
        layout.addWidget(self.footer)
        self._build_grips()

    @staticmethod
    def _command_button(text, tip):
        button = QPushButton(text)
        button.setObjectName('WidgetCommand')
        button.setAccessibleName(text)
        button.setToolTip(tip)
        button.setAccessibleDescription(tip)
        button.setAutoDefault(True)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _build_grips(self):
        for edge in ('left','right'):
            grip=EdgeGrip(edge,self);grip.drag_started.connect(self._grip_started);grip.dragged.connect(lambda dx,e=edge:self._grip_dragged(e,dx));grip.drag_finished.connect(self._grip_finished);self.grips[edge]=grip
        self._place_grips()
    def _place_grips(self):
        for edge,grip in self.grips.items():
            grip.setGeometry(0 if edge=='left' else self.width()-grip.width(),0,grip.width(),self.height());grip.raise_()
    def resizeEvent(self,e):
        super().resizeEvent(e);self._place_grips()
    def _load_width(self):
        try:value=int(self.db.get_setting('monitor_width','0') or 0)
        except ValueError:return 0
        return value if COMPACT_MIN_WIDTH<=value<=MAX_USER_WIDTH else 0
    def _screen_area(self):
        screen=self.screen() or QApplication.primaryScreen();return screen.availableGeometry() if screen else None
    def _grip_started(self):self._resize_origin=self.geometry()
    def _grip_dragged(self,edge,dx):
        """При изменении ширины противоположный край остается на месте."""
        origin=self._resize_origin
        if origin is None:return
        area=self._screen_area();limit=MAX_USER_WIDTH
        if area is not None:limit=min(limit,(origin.right()+1-area.left()) if edge=='left' else (area.right()+1-origin.left()))
        requested = origin.width() + (dx if edge == 'right' else -dx)
        self.user_width = max(COMPACT_MIN_WIDTH, min(limit, requested))
        self._fit_to_content()
        self.user_width = self.width()
        self.move(origin.left() if edge == 'right' else origin.right() + 1 - self.width(), self.y())
    def _grip_finished(self):
        self._resize_origin=None;self.db.set_setting('monitor_width',str(self.user_width))
    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton:self._drag=e.globalPosition().toPoint()-self.frameGeometry().topLeft()
        super().mousePressEvent(e)
    def mouseMoveEvent(self,e):
        if self._drag is not None and e.buttons()&Qt.MouseButton.LeftButton:self.move(e.globalPosition().toPoint()-self._drag)
        super().mouseMoveEvent(e)
    def mouseReleaseEvent(self,e):
        self._drag=None;super().mouseReleaseEvent(e)
    def _load_int(self,key,default,allowed):
        try:value=int(self.db.get_setting(key,str(default)) or default)
        except ValueError:value=default
        return value if value in allowed else default
    def _load_json(self,key):
        try:value=json.loads(self.db.get_setting(key,'{}') or '{}')
        except json.JSONDecodeError:value={}
        return value if isinstance(value,dict) else {}

    def _load_payments(self) -> dict[str, Payment]:
        payments = {}
        for provider, value in self._load_json(PAYMENTS_SETTING).items():
            if provider not in PROVIDERS or not isinstance(value, dict):
                continue
            date = value.get('date')
            amount = value.get('amount')
            if not isinstance(date, str) or not isinstance(amount, str) or not 0 < len(amount.strip()) <= PAYMENT_AMOUNT_LENGTH:
                continue
            try:
                payments[provider] = dt.date.fromisoformat(date), amount.strip()
            except ValueError:
                continue
        return payments
    def _visible(self,provider):return provider_enabled(provider) and bool(self.visibility.get(f'compact:{provider}',True))
    def _clear_rows(self):
        while self.rows_box.count():
            item=self.rows_box.takeAt(0);w=item.widget()
            if w:w.hide();w.setParent(None);w.deleteLater()
    def _init_cache_threads(self,scan):
        self._connection_generation = 0
        self.cache_scanner=ThreadScanner(codex_ttl=self._load_int('monitor_cache_codex_ttl',CODEX_TTL_DEFAULT,CACHE_TTL_CHOICES),grok_ttl=self._load_int('monitor_cache_grok_ttl',GROK_TTL_DEFAULT,CACHE_TTL_CHOICES));self.cache_enabled=self.db.get_setting('monitor_cache_threads','1')!='0'
        self.cache_expanded={p:True for p,v in self._load_json('monitor_cache_expanded').items() if p in CACHE_PROVIDERS and v is True};self.cache_show_all={}
        self.cache_threads={};self.cache_blocks={};self._cache_worker=None;self._cache_scan=scan;self.forecast_bars={};self.header_rows={};self.forecast_popup=None;self._popup_provider=None;self._clock_ticks=0;self._forecast_samples={}
        self.usage_report=EMPTY_REPORT;self._spend={};self._usage_worker=None;self.usage_scanner=UsageScanner() if scan else None
        self.usage_timer=QTimer(self);self.usage_timer.setInterval(USAGE_SCAN_MS);self.usage_timer.timeout.connect(self._start_usage_scan)
        self.cache_timer=QTimer(self);self.cache_timer.setInterval(CACHE_SCAN_MS);self.cache_timer.timeout.connect(self._start_cache_scan)
        if scan:self.cache_timer.start();QTimer.singleShot(400,self._start_cache_scan);self.usage_timer.start();QTimer.singleShot(1500,self._start_usage_scan)
    def _list_expanded(self):return self.cache_enabled and any(self.cache_expanded.get(p) for p in CACHE_PROVIDERS)
    def _cache_wanted(self):return self.cache_enabled and (self._cache_scan or self._list_expanded())
    def running_count(self,provider):
        '''Сколько тредов агента работает сейчас; None, пока сканирования не было или агент без тредов.'''
        if not self.cache_enabled or provider not in CACHE_PROVIDERS or provider not in self.cache_threads:return None
        return sum(1 for thread in self._shown_threads(provider) if thread.activity==ACTIVITY_WORKING)
    def _shown_threads(self,provider):
        '''Треды агента без тредов Claude Code из терминала, если они выключены в панели; счетчик работающих считает так же.'''
        threads=self.cache_threads.get(provider,[])
        return threads if self.thread_view.terminal else [t for t in threads if not is_claude_terminal(t)]
    def _update_running(self):
        changed = False
        for provider,header in self.header_rows.items():
            count=self.running_count(provider)
            if count != header.running:
                header.set_running(count)
                changed = True
        if changed:
            self._fit_to_content()
            QTimer.singleShot(0, self, self._fit_to_content)
    def _make_cache_block(self,provider):
        if not self.cache_enabled or not self.cache_expanded.get(provider):return None
        block=CacheThreadsBlock();block.more_clicked.connect(lambda p=provider:self.set_cache_show_all(p,True));block.less_clicked.connect(lambda p=provider:self.set_cache_show_all(p,False));block.restore_clicked.connect(lambda p=provider:self._restore_hidden(p));block.content_changed.connect(lambda:QTimer.singleShot(0,self,self._fit_to_content));self._fill_cache_block(provider,block);self.cache_blocks[provider]=block;return block
    def _fill_cache_block(self,provider,block):
        threads,hidden,pinned=self._apply_thread_rules(self._shown_threads(provider))
        return block.set_content(threads,dt.datetime.now(dt.timezone.utc),show_all=bool(self.cache_show_all.get(provider)),view=self.thread_view,pinned=pinned,hidden_count=hidden,spend=self._spend.get(provider))
    def _apply_thread_rules(self,threads):
        '''Скрытые треды убираются, закрепленные идут первыми, если включен учет правил.'''
        if not self.thread_view.pin_hide:return threads,0,frozenset()
        rules=self.thread_rules;shown=[t for t in threads if not rules.get(t.session_id,{}).get('hidden')]
        pinned=frozenset(t.session_id for t in shown if rules.get(t.session_id,{}).get('pinned'))
        shown.sort(key=lambda t:t.session_id not in pinned)
        return shown,len(threads)-len(shown),pinned
    def _refill_cache_blocks(self):
        changed=False
        for provider,block in self.cache_blocks.items():changed=self._fill_cache_block(provider,block) or changed
        if changed:self._fit_to_content();QTimer.singleShot(0,self,self._fit_to_content)
        return changed
    def _reload_thread_rules(self):
        self.thread_rules=self.db.thread_rules();self._refill_cache_blocks()
    def _restore_hidden(self,provider):
        for thread in self.cache_threads.get(provider,[]):
            if self.thread_rules.get(thread.session_id,{}).get('hidden'):self.db.set_hidden(thread.session_id,False)
        self._reload_thread_rules()
    def _make_drawer(self):
        """Панель вида тредов рядом с виджетом."""
        # Окно без фона со скругленной рамкой внутри: фон из стиля у прозрачного окна верхнего уровня не рисуется.
        drawer=QWidget(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint);drawer.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True);drawer.setFixedWidth(DRAWER_WIDTH);outer=QVBoxLayout(drawer);outer.setContentsMargins(0,0,0,0)
        body=QFrame();body.setObjectName('ThreadDrawer');outer.addWidget(body);box=QVBoxLayout(body);box.setContentsMargins(14,10,14,12);box.setSpacing(6);self.view_combos={}
        head=QHBoxLayout();head.setContentsMargins(0,0,0,2);heading=QLabel('Вид тредов');heading.setObjectName('DrawerHeading');close=QToolButton();close.setObjectName('PopupClose');close.setText('×');close.setAccessibleName('Закрыть настройки вида');close.setToolTip('Закрыть настройки вида');close.setCursor(Qt.CursorShape.PointingHandCursor);close.clicked.connect(drawer.hide);head.addWidget(heading,1);head.addWidget(close);box.addLayout(head)
        for spec in THREAD_VIEW_FIELDS:
            row=QHBoxLayout();row.setContentsMargins(14 if spec.indent else 0,0,0,0);text=QLabel(spec.label);text.setObjectName('DrawerSub' if spec.indent else 'DrawerLabel')
            if spec.choices:
                combo=QComboBox();combo.setObjectName('DrawerCombo');combo.setAccessibleName(spec.label)
                for value,label in spec.choices:combo.addItem(label,value)
                combo.setCurrentIndex(max(0,combo.findData(getattr(self.thread_view,spec.name))));combo.currentIndexChanged.connect(lambda _i,n=spec.name,c=combo:self._set_thread_view(n,c.currentData()));self.view_combos[spec.name]=combo;control=combo
            else:
                control=ToggleSwitch(bool(getattr(self.thread_view,spec.name)));control.setAccessibleName(spec.label);control.toggled.connect(lambda checked,n=spec.name:self._set_thread_view(n,checked));self.view_switches[spec.name]=control
            row.addWidget(text,1);row.addWidget(control);box.addLayout(row)
        row=QHBoxLayout();row.setContentsMargins(0,4,0,0);text=QLabel('Прозрачность окна');text.setObjectName('DrawerLabel');self.opacity_slider=QSlider(Qt.Orientation.Horizontal);self.opacity_slider.setObjectName('OpacitySlider');self.opacity_slider.setRange(2,10);self.opacity_slider.setPageStep(1);self.opacity_slider.setValue(max(2,min(10,self.opacity//10)));self.opacity_slider.setFixedWidth(96);self.opacity_slider.setAccessibleName('Прозрачность фона')
        self.opacity_value=QLabel(f'{self.opacity}%');self.opacity_value.setObjectName('DrawerSub');self.opacity_value.setFixedWidth(34);self.opacity_value.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter);self.opacity_slider.valueChanged.connect(self._set_opacity_step)
        row.addWidget(text,1);row.addWidget(self.opacity_slider);row.addWidget(self.opacity_value);box.addLayout(row)
        more=LinkButton('Все настройки');more.clicked.connect(self.open_settings);box.addWidget(more,0,Qt.AlignmentFlag.AlignLeft)
        drawer.setVisible(False);return drawer
    def font_delta(self):
        try:return float(self.thread_view.font)
        except ValueError:return 0.0
    def _apply_font(self):
        '''Размер шрифта из настройки: ко всем размерам стиля окна, панели и графика добавляется сдвиг в пунктах.'''
        style=scaled_style(self.font_delta());self.setStyleSheet(style);self.drawer.setStyleSheet(style)
        if self.forecast_popup is not None:self.forecast_popup.setStyleSheet(style)
    def _set_opacity_step(self,step):
        '''Ползунок прозрачности: шаг 10%, применяется сразу и запоминается (та же настройка, что в окне «Все настройки»).'''
        value=max(20,min(100,int(step)*10))
        if value==self.opacity:return
        self.opacity=value;self.opacity_value.setText(f'{value}%');self.db.set_setting('monitor_opacity',str(value));self._apply_opacity()
    def hide_to_tray(self):
        self.drawer.hide();self._close_popup();self.hide()
    def toggle_drawer(self):
        '''Открывает панель настроек слева от виджета (справа, если слева нет места) на высоте его верха; повторный щелчок закрывает.'''
        if self.drawer.isVisible():self.drawer.hide();return
        if hasattr(self,'opacity_slider'):self.opacity_slider.blockSignals(True);self.opacity_slider.setValue(max(2,min(10,self.opacity//10)));self.opacity_slider.blockSignals(False);self.opacity_value.setText(f'{self.opacity}%')
        self.drawer.adjustSize();area=self._screen_area();frame=self.frameGeometry();left=frame.left()-self.drawer.width()-8
        if area is not None and left<area.left():left=frame.right()+8
        top=frame.top()
        if area is not None:top=max(area.top(),min(top,area.bottom()+1-self.drawer.height()))
        self.drawer.move(left,top);self.drawer.show();self.drawer.raise_();self.drawer.activateWindow()
    def _set_thread_view(self,name,value):
        view=self.thread_view.with_value(name,value)
        if view==self.thread_view:return
        self.thread_view=view;self.db.set_setting(THREAD_VIEW_KEY,view.to_json())
        if name=='font':self._apply_font()
        self._render();self._start_usage_scan()
    def toggle_cache_provider(self,provider):
        expanded=not self.cache_expanded.get(provider,False)
        if expanded:self.cache_expanded[provider]=True
        else:self.cache_expanded.pop(provider,None);self.cache_show_all.pop(provider,None)
        self.db.set_setting('monitor_cache_expanded',json.dumps(self.cache_expanded,sort_keys=True))
        if expanded and self._cache_scan:self._start_cache_scan();self._start_usage_scan()
        self._render()
    def set_cache_show_all(self,provider,show_all):
        self.cache_show_all[provider]=bool(show_all);self._render()
    def _tick_cache(self):
        if self.cache_blocks:
            now=dt.datetime.now(dt.timezone.utc)
            for block in self.cache_blocks.values():block.tick(now)
    def weekly_of(self,provider):
        '''Недельное окно лимита провайдера (с процентом и временем сброса) или None.'''
        state=self.data.states.get(provider);return weekly_window(state.snapshot.windows) if state is not None else None
    def _usage_inputs(self):
        starts={};used={}
        for provider in ('claude','codex'):
            window=self.weekly_of(provider)
            if window is not None:starts[provider]=window.resets_at.astimezone(dt.timezone.utc)-WEEK;used[provider]=float(window.used_percent)
        return starts,used
    def _usage_wanted(self):return self.usage_scanner is not None and self._list_expanded() and not self._quit
    @Slot()
    def _start_usage_scan(self):
        if not self._usage_wanted() or self._usage_worker:return
        starts,used=self._usage_inputs();worker=UsageScanWorker(self.usage_scanner,starts,used);worker.generation=self._connection_generation;worker.signals.finished.connect(self._usage_scanned);worker.signals.failed.connect(self._usage_failed);self._usage_worker=worker;self.pool.start(worker)
    @Slot(object)
    def _usage_scanned(self,report):
        previous_worker = self._usage_worker
        self._usage_worker=None
        if self._quit:return
        if getattr(previous_worker, "generation", self._connection_generation) != self._connection_generation:
            self._start_usage_scan()
            return
        self.usage_report=report;self._spend={}
        for (agent,session_id),spend in report.threads.items():self._spend.setdefault(agent,{})[session_id]=spend
        self._refill_cache_blocks()
    @Slot(str)
    def _usage_failed(self,details):
        self._usage_worker=None;get_logger().error('AI Limits Monitor usage scan: %s',details)
    def _week_samples(self,provider,window):
        '''Замеры недели провайдера с ее начала (сброс минус 7 суток) для взвешенного темпа; из базы не чаще раза в FORECAST_SAMPLES_TTL секунд.'''
        start=window.resets_at.astimezone(dt.timezone.utc)-WEEK;cached=self._forecast_samples.get(provider);moment=time.monotonic()
        if cached is not None and cached[0]==start and moment-cached[1]<FORECAST_SAMPLES_TTL:return cached[2]
        try:samples=self.db.quota_samples(start_utc=start,provider=provider)
        except Exception:get_logger().exception('AI Limits Monitor forecast samples');samples=cached[2] if cached is not None and cached[0]==start else []
        self._forecast_samples[provider]=(start,moment,samples);return samples
    def _forecast_for(self,provider):
        window=self.weekly_of(provider)
        if window is None:return None
        return build_forecast(window,samples=self._week_samples(provider,window))
    def _make_forecast_bar(self,provider):
        if not self.thread_view.forecast:return None
        forecast=self._forecast_for(provider)
        if forecast is None:return None
        bar=ForecastBar();bar.set_forecast(forecast)
        bar.clicked.connect(lambda: self._forecast_clicked(provider))
        self.forecast_bars[provider]=bar;return bar
    def _refresh_forecasts(self):
        for provider,bar in self.forecast_bars.items():
            forecast=self._forecast_for(provider)
            if forecast is not None:bar.set_forecast(forecast)
    def _forecast_clicked(self, provider):
        bar = self.forecast_bars.get(provider)
        if self.thread_view.chart != 'click' or bar is None:
            return
        if self._popup_provider == provider:
            self._close_popup(restore_focus=True)
            return
        forecast = self._forecast_for(provider)
        if forecast is None:
            bar.set_chart_open(False)
            return
        previous = self.forecast_bars.get(self._popup_provider)
        if previous is not None:
            previous.set_chart_open(False)
        if self.forecast_popup is None:
            self.forecast_popup = ForecastPopup(self)
            self.forecast_popup.setStyleSheet(scaled_style(self.font_delta()))
            self.forecast_popup.closed.connect(lambda: self._close_popup(restore_focus=True))
        self._popup_provider = provider
        self._update_forecast_popup(provider, bar, forecast)
        self.forecast_popup.show()
        self.forecast_popup.raise_()
        self.forecast_popup.activateWindow()
        self.forecast_popup.close_button.setFocus()

    def _update_forecast_popup(self, provider, bar, forecast):
        bar.set_chart_open(True)
        samples = self._week_samples(provider, self.weekly_of(provider))
        popup = self.forecast_popup
        popup.set_data(forecast, build_chart(forecast, samples, self.usage_report.daily.get(provider, {})))
        popup.adjustSize()
        area = self._screen_area()
        frame = self.frameGeometry()
        left = frame.left() - popup.width() - 8
        top = bar.mapToGlobal(QPoint(0, 0)).y()
        if area is not None:
            if left < area.left():
                left = min(frame.right() + 8, area.right() + 1 - popup.width())
            left = max(area.left(), left)
            top = max(area.top(), min(top, area.bottom() + 1 - popup.height()))
        popup.move(left, top)

    def _close_popup(self, *, restore_focus=False):
        bar = self.forecast_bars.get(self._popup_provider)
        self._popup_provider = None
        if self.forecast_popup is not None:
            self.forecast_popup.hide()
        if bar is not None:
            bar.set_chart_open(False)
            if restore_focus:
                self.activateWindow()
                bar.chart_button.setFocus()
    @Slot()
    def _start_cache_scan(self):
        if not self._cache_wanted() or self._cache_worker or self._quit:return
        worker=CacheScanWorker(self.cache_scanner);worker.generation=self._connection_generation;worker.signals.finished.connect(self._cache_scanned);worker.signals.failed.connect(self._cache_failed);self._cache_worker=worker;self.pool.start(worker)
    @Slot(object)
    def _cache_scanned(self,found):
        previous_worker = self._cache_worker
        self._cache_worker=None
        if self._quit or not self.cache_enabled:return
        if getattr(previous_worker, "generation", self._connection_generation) != self._connection_generation:
            self._start_cache_scan()
            return
        self.cache_threads=found;self._refill_cache_blocks();self._update_running()
    @Slot(str)
    def _cache_failed(self,details):
        self._cache_worker=None;get_logger().error('AI Limits Monitor cache scan: %s',details)
    def set_cache_enabled(self,enabled):
        enabled=bool(enabled)
        if enabled==self.cache_enabled:return
        self.cache_enabled=enabled;self.db.set_setting('monitor_cache_threads','1' if enabled else '0');action=getattr(self,'cache_action',None)
        if action is not None and action.isChecked()!=enabled:action.setChecked(enabled)
        if self.cache_enabled and self._cache_scan:self.cache_timer.start();self._start_cache_scan()
        elif not self.cache_enabled:self.cache_timer.stop();self.cache_threads={}
        self._render()
    def _render(self):
        delta=self.font_delta();rows=[row for row in self.data.compact_rows if self._visible(row.provider)]
        signature = delta, tuple(row.provider for row in rows)
        rebuilt = signature != getattr(self, '_render_signature', None)
        if rebuilt:
            self._clear_rows()
            self.header_rows = {}
            self.cache_blocks = {}
            self.forecast_bars = {}
            self._render_signature = signature
        changed = rebuilt
        details_changed = False
        for row in rows:
            provider = row.provider
            state = self.data.states.get(provider) or ProviderState(provider, QuotaSnapshot(provider=provider), False, '')
            running = self.running_count(provider)
            payment = self.payments.get(provider)
            if rebuilt:
                header = CompactRow(row, state, running=running, font_delta=delta, payment=payment)
                self.header_rows[provider] = header
                self.rows_box.addWidget(AgentCard(header))
                if header.disclosure is not None:
                    header.disclosure.clicked.connect(lambda checked, p=provider: self.toggle_cache_provider(p))
            else:
                header = self.header_rows[provider]
                changed |= header.set_data(row, state, running)
                changed |= header.set_payment(payment)
            expanded = self.cache_enabled and header.disclosure is not None and bool(self.cache_expanded.get(provider))
            if header.disclosure is not None:
                header.disclosure.setVisible(self.cache_enabled)
                header.disclosure.set_expanded(expanded)
            block = self.cache_blocks.get(provider)
            if expanded and block is None:
                block = self._make_cache_block(provider)
            elif not expanded:
                self.cache_blocks.pop(provider, None)
                block = None
            bar = self.forecast_bars.get(provider)
            if block is not None and self.thread_view.forecast and self.weekly_of(provider):
                if bar is None:
                    bar = self._make_forecast_bar(provider)
            else:
                self.forecast_bars.pop(provider, None)
                bar = None
            details_changed |= header.parentWidget().set_details(bar, block)
        if not rebuilt:
            self._refill_cache_blocks()
            self._refresh_forecasts()
        for bar in self.forecast_bars.values():
            bar.set_chart_enabled(self.thread_view.chart == 'click')
        if self._popup_provider is not None:
            provider = self._popup_provider
            if self.thread_view.chart != 'click' or provider not in self.forecast_bars:
                self._close_popup()
            elif rebuilt or details_changed:
                self._update_forecast_popup(provider, self.forecast_bars[provider], self._forecast_for(provider))
        live=sum(s.live for s in self.data.states.values());local=sum((not s.live) and bool(s.snapshot.fallback_source) for s in self.data.states.values());cached=sum((not s.live) and not s.snapshot.fallback_source and s.snapshot.has_data() for s in self.data.states.values());self.footer.setText(f'{live}/4 онлайн'+(f' · {local} локальные' if local else '')+(f' · {cached} кэш' if cached else '')+f' · Codex/AG {refresh_label(self.seconds)}');self._update_tray_tooltip()
        if changed or details_changed:
            for header in self.header_rows.values():
                header.layout().invalidate();header.updateGeometry()
                if header.parentWidget() is not None:header.parentWidget().layout().invalidate();header.parentWidget().updateGeometry()
            self.rows_box.invalidate();self.centralWidget().layout().invalidate()
            was_visible=self.isVisible();self._fit_to_content()
            QTimer.singleShot(0,self,self._fit_to_content)
            if was_visible:QTimer.singleShot(FIT_SETTLE_MS,self,self._fit_to_content)
    def _update_clock(self):
        now = now_local()
        self.updated.setText(now.strftime('%H:%M'));self._tick_cache();self._clock_ticks+=1;self._refresh_countdowns(now)
        if self._clock_ticks%FORECAST_REFRESH_TICKS==0:self._refresh_forecasts()
    def _refresh_countdowns(self,now=None):
        '''Срок до сброса в заголовках агентов идет вместе с часами окна, без пересборки карточек. Если текст срока изменился (раз в минуту), окно подгоняется по высоте и ширине: сразу и еще раз через FIT_SETTLE_MS, как в _render, потому что вложенные раскладки отдают новый размер заголовка не сразу.'''
        now=now or dt.datetime.now(dt.timezone.utc);changed=False
        today = now.astimezone().date()
        for provider, row in self.header_rows.items():
            changed |= row.refresh_countdown(now)
            changed |= row.set_payment(self.payments.get(provider), today)
        if not changed:return
        self._fit_to_content()
        if self.isVisible():QTimer.singleShot(FIT_SETTLE_MS,self,self._fit_to_content)
    def _content_height(self, layout, width):
        layout.activate()
        return max(layout.totalMinimumSize().height(), layout.totalHeightForWidth(width))

    def _cap_cache_blocks(self, layout, width, area):
        blocks = list(self.cache_blocks.values())
        if not blocks or area is None:
            return
        for block in blocks:
            block.set_height_cap(None)
        excess = self._content_height(layout, width) - (area.height() - CACHE_SCREEN_MARGIN)
        if excess > 0:
            caps = distribute_height_caps([block.natural_height() for block in blocks],
                                          excess, MIN_VISIBLE_ROWS * ROW_HEIGHT)
            for block, cap in zip(blocks, caps):
                block.set_height_cap(cap)

    def _fit_to_content(self):
        before = self.geometry()
        layout = self.centralWidget().layout()
        target = self.user_width or DEFAULT_LIST_WIDTH
        area = self._screen_area()
        if area is not None:
            target = min(target, area.width())
        frame = layout.contentsMargins()
        for header in self.header_rows.values():
            card = header.parentWidget()
            border = card.contentsMargins()
            header.fit_width(target - frame.left() - frame.right() - border.left() - border.right())
            card.layout().invalidate()
            card.layout().activate()
            card.updateGeometry()
        self.rows_box.invalidate()
        layout.invalidate()
        layout.activate()
        minimum = max(COMPACT_MIN_WIDTH, layout.totalMinimumSize().width())
        preferred = DEFAULT_LIST_WIDTH if self.cache_blocks else min(DEFAULT_LIST_WIDTH, layout.totalSizeHint().width())
        width = max(minimum, self.user_width or preferred)
        if area is not None:
            width = min(width, max(minimum, area.width()))
        self._cap_cache_blocks(layout, width, area)
        height = max(COMPACT_MIN_HEIGHT, self._content_height(layout, width))
        self.setMinimumSize(minimum, height)
        self.setGeometry(self._anchored_geometry(before, width, height))

    def _anchored_geometry(self,current,width,height):
        '''Правый нижний угол остается на месте: окно растет вверх и влево, у края экрана упирается в него.'''
        if current.width()==width and current.height()==height:return current
        screen=self.screen() or QApplication.primaryScreen();area=screen.availableGeometry() if screen else None
        left=current.right()+1-width;top=current.bottom()+1-height
        if area is not None:left=max(area.left(),left);top=max(area.top(),top)
        return QRect(left,top,width,height)
    @Slot()
    def refresh_now(self):self.refresh()
    @Slot()
    def refresh(self):
        if self.worker:return
        worker=RefreshWorker(self.db);worker.signals.finished.connect(self._done);worker.signals.failed.connect(self._failed);self.worker=worker;self.pool.start(worker)
    @Slot(object)
    def _done(self,data):
        self.worker=None
        if self._quit:
            self._finish_quit()
            return
        previous=self._usage_inputs()
        if isinstance(data,MonitorData):self.data=data;self.states=data.states;self.rows=data.rows
        self._render()
        if self._usage_inputs()!=previous:self._start_usage_scan()
    @Slot(str)
    def _failed(self,details):
        self.worker=None
        if self._quit:
            self._finish_quit()
            return
        get_logger().error('AI Limits Monitor refresh: %s',details);self.footer.setText('ошибка обновления · последние данные')
    def _finish_quit(self):
        app=QApplication.instance()
        if app:app.quit()
    @staticmethod
    def _enable_transparency(widget):
        """Keep the native window and its rounded surface genuinely translucent."""
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        widget.setAutoFillBackground(False)

    def _enforce_topmost(self):
        if self.isVisible():set_window_always_on_top(self,self.topmost)
    def _apply_window_flags(self):
        flags=Qt.WindowType.FramelessWindowHint|Qt.WindowType.Tool
        if self.topmost:flags|=Qt.WindowType.WindowStaysOnTopHint
        visible=self.isVisible();self.setWindowFlags(flags);self._enable_transparency(self);self._enable_transparency(self.centralWidget())
        if visible:self.show()
        QTimer.singleShot(0,self,self._enforce_topmost)
    def _apply_opacity(self):
        '''Прозрачность всего окна вместе с текстом: фон плотный, поэтому текст остается четким (ClearType).'''
        shell=self.centralWidget();self._enable_transparency(self);self._enable_transparency(shell)
        shell.setStyleSheet("QFrame#WidgetShell{background:transparent;border:none;}");self.setWindowOpacity(max(0.2,min(1.0,self.opacity/100)))
    def open_settings(self):
        mode=self.db.get_setting('monitor_ag_mode','auto_today') or 'auto_today';fixed=self.db.get_setting('monitor_ag_fixed_model','') or ''
        d=SettingsDialog(self.data,self.visibility,self.seconds,self.topmost,self.opacity,mode,fixed,self,payments=self.payments)
        d.connections_requested.connect(self.open_connections)
        d.setStyleSheet(QApplication.instance().styleSheet()+STYLE)
        if d.exec()!=QDialog.DialogCode.Accepted:return
        if self.payments != d.payments:
            stored = {provider: {'date': date.isoformat(), 'amount': amount} for provider, (date, amount) in d.payments.items()}
            self.db.set_setting(PAYMENTS_SETTING, json.dumps(stored, ensure_ascii=False))
            self.payments = d.payments
        self.seconds,self.topmost,self.opacity,vis,mode,fixed=d.values();self.visibility.update(vis);self.db.set_setting('monitor_refresh_seconds',str(self.seconds));self.db.set_setting('monitor_always_on_top','1' if self.topmost else '0');self.db.set_setting('monitor_opacity',str(self.opacity));self.db.set_setting('monitor_compact_visibility',json.dumps(self.visibility,sort_keys=True));self.db.set_setting('monitor_ag_mode',mode);self.db.set_setting('monitor_ag_fixed_model',fixed);self.timer.setInterval(self.seconds*1000);self._apply_window_flags();self._apply_opacity();compact,selected=build_compact_rows(self.db,self.states,force_ag_scan=True);self.data.compact_rows=compact;self.data.antigravity_selected_id=selected;self._render()
    def open_connections(self):
        from .connections_dialog import ConnectionsDialog
        previous = load_connections()
        accepted = ConnectionsDialog(self).exec() == QDialog.DialogCode.Accepted
        changed = previous != load_connections()
        if changed:
            self._connection_generation += 1
            self.cache_threads.clear()
            self._spend.clear()
            self._forecast_samples.clear()
            self.usage_report = EMPTY_REPORT
            states = cached_states(self.db)
            compact, selected = build_compact_rows(self.db, states)
            self.states = states
            self.rows = build_rows(states)
            self.data = MonitorData(states, self.rows, compact, selected)
            self._render()
            self._start_cache_scan()
            self._start_usage_scan()
        if changed or accepted:
            self.refresh_now()

    def configure_claude_statusline(self):
        answer = QMessageBox.question(
            self, 'Claude · локальная интеграция',
            'Подключить официальную строку состояния Claude Code к AI Limits?\n\n'
            'Веб-сессию, cookie и OAuth приложение не использует. '
            'Изменится только пользовательский settings.json Claude Code. '
            'Другие настройки и существующая чужая строка состояния сохранятся.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = install_statusline_command(bridge_command())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Claude · локальная интеграция', str(error))
            return
        if result == 'conflict':
            QMessageBox.warning(
                self, 'Claude · локальная интеграция',
                'В Claude Code уже настроена своя строка состояния. '
                'Она не изменена. Для объединения потребуется вручную добавить '
                'локальный обработчик AI Limits в существующую команду.',
            )
        elif result == 'already':
            QMessageBox.information(self, 'Claude · локальная интеграция',
                'Подключение уже записано. Проценты появятся после первого ответа '
                'Claude Code с подпиской Pro или Max.')
        else:
            QMessageBox.information(
                self, 'Claude · локальная интеграция',
                'Настройка сохранена.\n\n'
                'Проценты появятся только после первого ответа модели в Claude Code '
                'для подписок Pro/Max. Пока их нет, виджет показывает прочерк.\n'
                'AI Limits не выполняет собственные запросы к Anthropic.',
            )

    def show_claude_connection(self):
        """Explain local readiness without touching a Claude account or network."""
        try:
            path = claude_root() / "settings.json"
            settings = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
            entry = settings.get("statusLine", {}) if isinstance(settings, dict) else {}
            configured = (
                isinstance(entry, dict)
                and entry.get("type") == "command"
                and entry.get("command") == bridge_command()
            )
            snapshot_file = statusline_path()
            snapshot_exists = snapshot_file.is_file()
            snapshot = read_statusline_quota()
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, "Claude · проверка данных",
                                f"Не удалось проверить локальный источник: {error}")
            return

        if not configured:
            status = (
                "Источник не подключён к текущей версии AI Limits. "
                "Выбери «Claude · подключить локальные лимиты»."
            )
        elif snapshot.has_data():
            stamp = (
                snapshot.updated_at.astimezone().strftime("%d.%m.%Y %H:%M")
                if snapshot.updated_at else "неизвестно"
            )
            remaining_5h = (
                f"{100 - snapshot.five_hour:.0f}%"
                if snapshot.five_hour is not None else "—"
            )
            remaining_7d = (
                f"{100 - snapshot.weekly:.0f}%"
                if snapshot.weekly is not None else "—"
            )
            status = (
                f"Последний снимок Claude Code: {stamp}.\n"
                f"Осталось за 5 часов: {remaining_5h}.\n"
                f"Осталось за 7 дней: {remaining_7d}."
            )
            if snapshot.fallback_source:
                status += "\nСнимок устарел: ожидается новое событие Claude Code."
        elif not snapshot_exists:
            status = (
                "Настройка сохранена, но данных от Claude Code ещё не получено.\n"
                "Запусти Claude Code, дождись первого ответа модели и обнови AI Limits."
            )
        else:
            status = (
                "Обработчик запускался, но актуальных лимитов в снимке нет.\n"
                "Поля квот появляются для Pro/Max после первого ответа модели. "
                "Claude Desktop и Claude Code могут передавать данные по-разному."
            )
        QMessageBox.information(
            self, "Claude · проверка данных",
            status + "\n\nПроверка выполнена только по локальным файлам, без сети.",
        )

    def configure_claude_manual_resets(self):
        dialog = ClaudeManualResetsDialog(manual_resets_edit_text(self.db), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            count = save_manual_resets(self.db, dialog.value_text())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Claude · сбросы вручную", str(error))
            return
        QMessageBox.information(
            self, "Claude · сбросы вручную",
            f"Сохранено бонусных сбросов: {count}.\n"
            "Информация введена вручную и не проверялась через Anthropic.",
        )
        self.refresh()

    def open_detail(self):
        # The unified application overrides this method and owns the analytics window.
        return None
    def _build_tray(self):
        self.tray = QSystemTrayIcon(QIcon(str(ensure_icon())), self)
        menu = QMenu(self)
        menu.addAction('Настроить вид', self.toggle_drawer)
        menu.addAction('Все настройки', self.open_settings)
        menu.addAction('Подключения и вход', self.open_connections)
        self.cache_action = menu.addAction('Таймеры кэша тредов')
        self.cache_action.setCheckable(True)
        self.cache_action.setChecked(self.cache_enabled)
        self.cache_action.toggled.connect(self.set_cache_enabled)
        menu.addSeparator()
        menu.addAction('Показать', self.show_normal)
        menu.addAction('Обновить', self.refresh_now)
        menu.addAction('Детальная аналитика', self.open_detail)
        menu.addSeparator()
        menu.addAction('Claude · подключить локальные лимиты...', self.configure_claude_statusline)
        menu.addAction('Claude · проверить источник...', self.show_claude_connection)
        menu.addAction('Claude · сбросы вручную...', self.configure_claude_manual_resets)
        menu.addSeparator()
        menu.addAction('Выход', self.quit_monitor)
        self.tray.setContextMenu(menu)
        self.menu_button.setMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_normal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        self._update_tray_tooltip()
    def _update_tray_tooltip(self):
        if not hasattr(self,'tray'):return
        parts=[]
        for row in self.data.compact_rows:
            state=self.data.states.get(row.provider);status=state_status(state);c=primary_cell(row)
            parts.append(f'{row.name}: {percent_text(c.remaining if c else None)} ({status})')
        self.tray.setToolTip('AI Limits · осталось\n'+' · '.join(parts[:4]))
    def show_normal(self):self.show();self._enforce_topmost();self.raise_();self.activateWindow();QTimer.singleShot(0,self._enforce_topmost)
    def quit_monitor(self):
        self._quit=True;self.tray.hide();self.timer.stop();self.clock_timer.stop();self.cache_timer.stop();self.usage_timer.stop();self._close_popup();self.drawer.hide();self.drawer.deleteLater()
        if self.forecast_popup is not None:self.forecast_popup.deleteLater();self.forecast_popup=None
        if self.worker is None:self._finish_quit()
    def _restore_geometry(self):
        raw=self.db.get_state('monitor_geometry_compact','') or ''
        if raw:
            try:self.restoreGeometry(QByteArray.fromHex(raw.encode('ascii')))
            except Exception:pass
    def closeEvent(self,event:QCloseEvent):
        self.db.set_state('monitor_geometry_compact',bytes(self.saveGeometry().toHex()).decode('ascii'))
        if self._quit:super().closeEvent(event)
        else:self._close_popup();self.drawer.hide();event.ignore();self.hide()

STYLE=f'''
*{{font-family:"Segoe UI";}}
QLabel#WidgetTitle{{font-family:"Segoe UI";font-size:10.5pt;font-weight:700;color:{COLORS['text']};}}
QLabel#UpdatedLabel,QLabel#WidgetFooter{{color:{COLORS['faint']};font-size:7.5pt;}}
QLabel#WidgetFooter{{padding-left:6px;}}
QPushButton#WidgetCommand,QPushButton#HideWidget,QToolButton#CardDisclosure{{
    background:{COLORS['panel_alt']};color:{COLORS['text']};border:1px solid {COLORS['border']};border-radius:5px;
}}
QPushButton#WidgetCommand{{min-height:26px;padding:0;font-size:9pt;}}
QPushButton#WidgetCommand::menu-indicator{{width:8px;height:8px;}}
QPushButton#HideWidget{{min-height:22px;padding:0 6px;font-size:8.5pt;color:{COLORS['muted']};}}
QToolButton#CardDisclosure{{padding:0;}}
QPushButton#WidgetCommand:hover,QPushButton#HideWidget:hover,QToolButton#CardDisclosure:hover,QPushButton#CacheLink:hover{{
    background:{COLORS['elevated']};color:{COLORS['text']};border-color:{COLORS['accent_hover']};
}}
QPushButton#WidgetCommand:pressed,QPushButton#HideWidget:pressed,QToolButton#CardDisclosure:pressed,
QToolButton#CardDisclosure:checked,QPushButton#CacheLink:pressed{{background:{COLORS['selection']};}}
QPushButton#WidgetCommand:focus,QPushButton#HideWidget:focus,QToolButton#CardDisclosure:focus,QPushButton#CacheLink:focus{{
    border-color:#91BDF0;
}}
QFrame#AgentCard{{background:rgba(255,255,255,10);border:1px solid rgba(120,140,160,70);border-radius:12px;}}
QFrame#CompactRow{{background:transparent;border:none;border-radius:0;}}
QFrame#CompactRow[divider="true"]{{border-bottom:1px solid rgba(70,88,106,120);}}
QLabel#AgentName{{background:transparent;color:{COLORS['text']};font-size:10.5pt;font-weight:600;}}
QLabel#AgentPayment{{background:transparent;color:{COLORS['muted']};font-size:8pt;}}
QLabel#ResetNotice{{background:rgba(224,176,74,40);color:#E0B04A;font-size:8.5pt;border-radius:6px;padding:3px 8px;}}
QLabel#ResetNotice[critical="true"]{{background:rgba(229,103,94,48);color:#E5675E;}}
QFrame#QuotaColumns{{background:rgba(100,132,163,24);border:none;border-radius:6px;}}
QFrame#QuotaColumns QLabel{{background:transparent;}}
QFrame#Forecast{{background:transparent;border:none;}}
QFrame#Forecast[divider="true"]{{border-bottom:1px solid rgba(70,88,106,120);}}
QLabel#ForecastAxis{{background:transparent;color:{COLORS['faint']};font-size:8.25pt;}}
QFrame#Chip{{background:rgba(120,140,160,38);border:none;border-radius:5px;}}
QLabel#ChipText{{background:transparent;color:{COLORS['muted']};font-size:8.25pt;}}
QFrame#ChipDanger{{background:rgba(229,103,94,48);border:none;border-radius:5px;}}
QLabel#ChipTextDanger{{background:transparent;color:#E5675E;font-size:8.25pt;}}
QFrame#ForecastPopup{{background:{COLORS['panel']};border:1px solid {COLORS['border']};border-radius:12px;}}
QLabel#PopupHeading{{background:transparent;color:{COLORS['text']};font-size:10.5pt;font-weight:600;}}
QToolButton#PopupClose{{background:{COLORS['panel_alt']};border:1px solid {COLORS['border']};border-radius:5px;color:{COLORS['text']};font-size:13pt;padding:0 4px;min-width:20px;min-height:24px;}}
QToolButton#PopupClose:hover{{background:{COLORS['elevated']};}}
QToolButton#PopupClose:focus{{border-color:#91BDF0;}}
QFrame#PopupTile{{background:{COLORS['panel_alt']};border:none;border-radius:8px;}}
QLabel#TileCaption{{background:transparent;color:{COLORS['muted']};font-size:8.25pt;}}
QLabel#TileValue{{background:transparent;color:{COLORS['text']};font-size:10.5pt;font-weight:600;}}
QLabel#TileValue[short="true"]{{color:#E5675E;}}
QWidget#PopupLegend{{background:transparent;}}
QLabel#LegendText{{background:transparent;color:{COLORS['text']};font-size:9pt;}}
QLabel#PopupInfo{{background:transparent;color:{COLORS['muted']};font-size:9pt;}}
QLabel#PopupNote{{background:transparent;color:{COLORS['faint']};font-size:8pt;}}
QLabel#OnlineDot{{color:{COLORS['success']};font-size:8.5pt;}}
QLabel#LocalDot{{color:{COLORS['accent_hover']};font-size:8.5pt;}}
QLabel#CacheDot{{color:{COLORS['warning']};font-size:8.5pt;}}
QLabel#OfflineDot{{color:{COLORS['faint']};font-size:8.5pt;}}
QFrame#AgPopupBox{{background:{COLORS['control']};border:1px solid {COLORS['border']};border-radius:10px;}}
QLabel#PopupTitle{{font-weight:700;color:{COLORS['text']};font-size:9pt;}}
QFrame#PopupLine{{background:transparent;border:none;}}
QFrame#PopupLineSelected{{background:{COLORS['selection']};border-radius:6px;}}
QLabel#PopupModel{{color:{COLORS['muted']};font-size:8.5pt;}}
QLabel#SettingsHint{{color:{COLORS['muted']};font-size:9pt;}}
QLineEdit#PaymentInput{{min-height:24px;}}
QFrame#CacheBlock{{background:transparent;border:none;border-radius:0;}}
QFrame#CacheRow{{background:transparent;border:none;border-bottom:1px solid rgba(70,88,106,120);border-radius:0;}}
QFrame#CacheRow[last="true"]{{border-bottom:none;}}
QFrame#CacheRow:hover{{background:rgba(43,62,80,110);}}
#CacheScroll,QWidget#CacheBody,QWidget#qt_scrollarea_viewport{{background:transparent;border:none;}}
#CacheScroll QScrollBar:vertical{{background:transparent;width:6px;margin:0;}}
#CacheScroll QScrollBar::handle:vertical{{background:{COLORS['accent']};border-radius:3px;min-height:20px;}}
#CacheScroll QScrollBar::add-line:vertical,#CacheScroll QScrollBar::sub-line:vertical{{height:0;}}
QLabel#CacheTitle{{background:transparent;color:{COLORS['text']};font-size:10.25pt;font-weight:500;}}
QLabel#CacheProject{{background:transparent;color:#9AA6B2;font-size:9pt;}}
QLabel#CacheWarn{{background:transparent;color:#E0B04A;font-size:9pt;}}
QFrame#CacheRow[warn="true"]{{background:rgba(224,176,74,34);}}
QLabel#CacheEnv{{background:{COLORS['elevated']};color:{COLORS['muted']};font-size:7pt;border-radius:4px;padding:0px 5px;}}
QLabel#CacheMore{{background:transparent;color:{COLORS['faint']};font-size:7.5pt;}}
QPushButton#CacheLink{{background:{COLORS['panel_alt']};color:#C5D9ED;border:1px solid {COLORS['border']};border-radius:5px;min-height:22px;padding:0 7px;font-size:9pt;}}
QFrame#ThreadDrawer{{background:{COLORS['panel']};border:1px solid {COLORS['border']};border-radius:12px;}}
QLabel#DrawerHeading{{background:transparent;color:{COLORS['text']};font-size:10pt;font-weight:600;}}
QSlider#OpacitySlider::groove:horizontal{{height:4px;background:#3A4656;border-radius:2px;}}
QSlider#OpacitySlider::sub-page:horizontal{{background:#4C9AF0;border-radius:2px;}}
QSlider#OpacitySlider::handle:horizontal{{background:#F3F6FA;width:12px;height:12px;margin:-4px 0;border-radius:6px;}}
QLabel#DrawerSub{{background:transparent;color:{COLORS['muted']};font-size:8.5pt;}}
QComboBox#DrawerCombo{{background:{COLORS['panel_alt']};border:1px solid {COLORS['border_soft']};border-radius:6px;padding:1px 8px;font-size:8.5pt;min-height:20px;}}
QComboBox#DrawerCombo QAbstractItemView{{background:{COLORS['panel']};selection-background-color:{COLORS['selection']};}}
QLabel#DrawerLabel{{background:transparent;color:{COLORS['text']};font-size:8.5pt;}}
'''
_FONT_SIZE=re.compile(r'font-size:([0-9]+(?:\.[0-9]+)?)pt')
def scaled_style(delta):
    '''Стиль окна со сдвигом всех размеров шрифта на `delta` пунктов (настройка «Размер шрифта»).'''
    if not delta:return STYLE
    return _FONT_SIZE.sub(lambda match:f'font-size:{max(6.0,float(match.group(1))+delta):g}pt',STYLE)

def _mutex():
    if sys.platform!='win32':return True
    k=ctypes.windll.kernel32;k.CreateMutexW.restype=ctypes.c_void_p;h=k.CreateMutexW(None,False,_MUTEX_NAME)
    if k.GetLastError()==_ALREADY_EXISTS:
        if h:k.CloseHandle(h)
        return None
    return h

def run_monitor():
    setup_logging(logging.INFO);dpi_mode=enable_per_monitor_dpi_awareness();m=_mutex()
    if m is None:return 0
    db=Database();app=QApplication.instance() or QApplication(sys.argv);font=app.font();font.setHintingPreference(QFont.HintingPreference.PreferFullHinting);app.setFont(font);app.setApplicationName(MONITOR_NAME);app.setApplicationVersion(MONITOR_VERSION);app.setQuitOnLastWindowClosed(False);app.setStyle('Fusion');app.setStyleSheet(build_stylesheet()+STYLE);get_logger().info('AI Limits Monitor DPI awareness=%s, screen DPR=%.2f',dpi_mode,float(app.primaryScreen().devicePixelRatio() if app.primaryScreen() else 1.0));w=MonitorWindow(db);w.show_normal()
    try:return app.exec()
    finally:
        db.close()
        if sys.platform=='win32' and m not in (None,True):ctypes.windll.kernel32.CloseHandle(m)

def main():
    try:return run_monitor()
    except Exception:get_logger().error('AI Limits Monitor fatal: %s',traceback.format_exc());return 1
