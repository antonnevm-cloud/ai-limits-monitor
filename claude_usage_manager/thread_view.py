"""Настройки вида списка тредов: что показывать в строке и какие функции включены."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

SETTING_KEY = "monitor_thread_view"

USAGE_MODES = (("off", "нет"), ("pct", "% недели"), ("usd", "доллары"), ("both", "оба"))
CHART_MODES = (("click", "по щелчку"), ("off", "выключен"))
FONT_SIZES = (("-1", "мельче"), ("0", "как было"), ("+0.5", "крупнее на 0,5"), ("+1", "крупнее на 1"), ("+1.5", "крупнее на 1,5"), ("+2", "крупнее на 2"))


@dataclass(frozen=True)
class ThreadView:
    """Глобальные переключатели списка тредов; по умолчанию включено все, кроме тредов Claude Code из терминала."""

    compact: bool = True  # компактный вид: тред в две строки (название; проект и короткие чипы), кольцо меньше
    timer: bool = True  # время жизни кэша (кольцо с таймером)
    title: bool = True  # название треда
    project: bool = True  # проект и среда
    usage: str = "both"  # расход треда: off, pct (% недели), usd (доллары), both
    rewrite: bool = True  # цена остывания: во что обойдется перезапись кэша
    warning: bool = True  # предупреждение перед остыванием кэша
    glutton: bool = True  # подсветка самого прожорливого треда
    pin_hide: bool = True  # учитывать сохраненные правила тредов
    terminal: bool = False  # треды Claude Code из терминала (по умолчанию скрыты, решение владельца 12.11.2026)
    forecast: bool = True  # прогноз недели
    chart: str = "click"  # график расхода по щелчку, off скрывает команду
    font: str = "0"  # сдвиг размера шрифта окна в пунктах (названия тредов уже на полпункта крупнее)

    @classmethod
    def from_json(cls, raw: str | None) -> "ThreadView":
        """Читает настройку; испорченное или чужого типа значение заменяется умолчанием, лишние ключи игнорируются."""
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        default = cls()
        values: dict[str, Any] = {}
        for item in fields(cls):
            fallback = getattr(default, item.name)
            value = data.get(item.name)
            choices = CHOICES.get(item.name)
            if choices is not None:
                values[item.name] = value if isinstance(value, str) and value in dict(choices) else fallback
            else:
                values[item.name] = value if isinstance(value, bool) else fallback
        return cls(**values)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    def with_value(self, name: str, value: Any) -> "ThreadView":
        """Копия с одним измененным полем; имя и значение проверяются."""
        if name not in {item.name for item in fields(self)}:
            raise ValueError(name)
        choices = CHOICES.get(name)
        if choices is not None:
            if value not in dict(choices):
                raise ValueError(f"{name}={value!r}")
            return replace(self, **{name: value})
        return replace(self, **{name: bool(value)})


CHOICES: dict[str, tuple[tuple[str, str], ...]] = {"usage": USAGE_MODES, "chart": CHART_MODES, "font": FONT_SIZES}


@dataclass(frozen=True)
class FieldSpec:
    """Одна строка выезжающей панели: переключатель или выбор из списка."""

    name: str
    label: str
    indent: bool = False

    @property
    def choices(self) -> tuple[tuple[str, str], ...] | None:
        return CHOICES.get(self.name)


# Порядок и подписи строк панели как в макете.
FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("compact", "Компактно"),
    FieldSpec("timer", "Время жизни кэша"),
    FieldSpec("title", "Название треда"),
    FieldSpec("project", "Проект и среда"),
    FieldSpec("usage", "Расход треда"),
    FieldSpec("rewrite", "Цена остывания"),
    FieldSpec("warning", "Предупреждение"),
    FieldSpec("glutton", "Подсветка прожорливого"),
    FieldSpec("pin_hide", "Учитывать правила тредов"),
    FieldSpec("terminal", "Claude Code из терминала"),
    FieldSpec("forecast", "Прогноз недели"),
    FieldSpec("chart", "График расхода", indent=True),
    FieldSpec("font", "Размер шрифта"),
)
