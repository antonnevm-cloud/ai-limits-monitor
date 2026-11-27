"""Модели данных приложения (dataclasses)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Period:
    """Выбранный период анализа."""
    key: str
    label: str
    start_local: dt.datetime
    end_local: dt.datetime

    @property
    def start_utc(self) -> dt.datetime:
        return self.start_local.astimezone(dt.timezone.utc)

    @property
    def end_utc(self) -> dt.datetime:
        return self.end_local.astimezone(dt.timezone.utc)

    @property
    def hours(self) -> float:
        return (self.end_local - self.start_local).total_seconds() / 3600.0

    @property
    def hourly(self) -> bool:
        """<=48 часов — разбивка по часам, иначе по дням."""
        return self.hours <= 48.0


@dataclass
class ThreadRow:
    """Строка треда (может быть объединённой группой нескольких сессий)."""
    session_id: str
    title: str
    project: str
    agent: str = "claude"
    project_path: str = ""
    models: list[str] = field(default_factory=list)
    cost: float = 0.0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    first_activity: dt.datetime | None = None
    last_activity: dt.datetime | None = None
    subagents: int = 0
    is_service: bool = False
    hidden: bool = False
    pinned: bool = False
    group_name: str = ""
    is_group: bool = False
    member_ids: list[str] = field(default_factory=list)
    # заполняется на этапе агрегации
    share: float = 0.0
    weekly_pp: float | None = None
    quota_pp_by_window: dict[str, float] = field(default_factory=dict)
    limit_pp_text: str = ""


@dataclass
class ProjectRow:
    project: str
    threads: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    share: float = 0.0
    quota_pp_by_window: dict[str, float] = field(default_factory=dict)
    limit_pp_text: str = ""


@dataclass
class ModelRow:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    share: float = 0.0
    quota_percent: float | None = None
    quota_pp: float | None = None
    quota_reset: dt.datetime | None = None
    quota_window_id: str = ""


@dataclass
class TimelineBucket:
    """Корзина по часам или дням. Стоимость — оценочная."""
    label: str
    start: dt.datetime
    total_tokens: int = 0
    estimated_cost: float = 0.0


@dataclass
class QuotaWindow:
    """Одно независимое окно/пул лимита провайдера."""
    id: str
    title: str
    used_percent: float | None = None
    resets_at: dt.datetime | None = None
    window_minutes: int | None = None
    role: str = ""
    informational: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ManualResetItem:
    """Один расходуемый вручную кредит полного сброса лимитов."""
    id: str = ""
    provider: str = ""
    expires_at: dt.datetime | None = None
    granted_at: dt.datetime | None = None
    status: str = "available"
    reset_type: str = ""
    title: str = ""
    description: str = ""

    def remaining_seconds(self, now: dt.datetime | None = None) -> int | None:
        if self.expires_at is None:
            return None
        current = now or dt.datetime.now(dt.timezone.utc)
        expiry = self.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=dt.timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=dt.timezone.utc)
        seconds = (
            expiry.astimezone(dt.timezone.utc) - current.astimezone(dt.timezone.utc)
        ).total_seconds()
        return max(0, int(seconds))


@dataclass
class ManualResetCredits:
    """Кредиты ручного сброса, не являющиеся quota window."""
    provider: str = ""
    state: str = "unknown"
    available_count: int | None = None
    items: list[ManualResetItem] = field(default_factory=list)
    source: str = ""
    updated_at: dt.datetime | None = None
    freshness: str = "unknown"
    details_complete: bool = False
    nearest_expiry_hint: dt.datetime | None = None
    error: str = ""

    def sorted_items(self) -> list[ManualResetItem]:
        ceiling = dt.datetime.max.replace(tzinfo=dt.timezone.utc)

        def expiry_key(item: ManualResetItem) -> dt.datetime:
            if item.expires_at is None:
                return ceiling
            if item.expires_at.tzinfo is None:
                return item.expires_at.replace(tzinfo=dt.timezone.utc)
            return item.expires_at.astimezone(dt.timezone.utc)

        return sorted(
            self.items,
            key=expiry_key,
        )

    def nearest_expiry(self, now: dt.datetime | None = None) -> dt.datetime | None:
        """Ближайший срок еще действующего кредита: истекший первый не скрывает следующий."""
        current = now or dt.datetime.now(dt.timezone.utc)
        for item in self.sorted_items():
            # Использованный или отозванный кредит не считается доступным.
            if item.status.casefold() not in {"available", "unknown", ""}:
                continue
            if item.expires_at is not None and item.remaining_seconds(current):
                return item.expires_at
        hint = self.nearest_expiry_hint
        if hint is not None and ManualResetItem(expires_at=hint).remaining_seconds(current):
            return hint
        return None


@dataclass
class QuotaSnapshot:
    """Нормализованный снимок произвольного набора quota windows.

    Legacy-поля five_hour/weekly/fable_weekly сохранены для совместимости со
    старыми отчётами и SQLite v1/v2. Новая логика использует ``windows`` и
    ``main_window_id``.
    """
    five_hour: float | None = None
    weekly: float | None = None
    fable_weekly: float | None = None
    source: str = ""
    provider: str = ""
    login_method: str = ""
    five_hour_resets_at: dt.datetime | None = None
    weekly_resets_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    main_label: str = ""
    secondary_label: str = ""
    extra_label: str = ""
    error: str = ""
    windows: list[QuotaWindow] = field(default_factory=list)
    main_window_id: str = ""
    manual_resets: ManualResetCredits = field(default_factory=ManualResetCredits)
    # Заполняется только для текущего снимка, восстановленного из локального
    # источника вместо live-ответа. В SQLite намеренно не сохраняется.
    fallback_source: str = ""

    def window(self, window_id: str) -> QuotaWindow | None:
        return next((window for window in self.windows if window.id == window_id), None)

    def main_window(self) -> QuotaWindow | None:
        if self.main_window_id:
            found = self.window(self.main_window_id)
            if found is not None and found.used_percent is not None and not found.informational:
                return found
        return next(
            (window for window in self.windows
             if window.used_percent is not None and not window.informational),
            None,
        )

    def has_data(self) -> bool:
        return any(window.used_percent is not None and not window.informational for window in self.windows) or (
            self.five_hour is not None
            or self.weekly is not None
            or self.fable_weekly is not None
        )


@dataclass
class AnalysisResult:
    period: Period
    agent: str = "claude"
    threads: list[ThreadRow] = field(default_factory=list)
    projects: list[ProjectRow] = field(default_factory=list)
    model_rows: list[ModelRow] = field(default_factory=list)
    timeline: list[TimelineBucket] = field(default_factory=list)
    total_tokens: int = 0
    total_cost: float = 0.0
    work_threads: int = 0
    quota: QuotaSnapshot = field(default_factory=QuotaSnapshot)
    weekly_consumed_pp: float | None = None
    quota_consumed_by_window: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    generated_at: dt.datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # Полный набор доступных тредов выбранного периода до текстового поиска.
    all_threads: list[ThreadRow] = field(default_factory=list)
