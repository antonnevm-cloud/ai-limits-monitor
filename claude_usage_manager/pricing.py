"""Цены API: во сколько обошлись бы токены, если бы шли через API, а не по подписке.

Цифры взяты только с официальных страниц на 12.11.2026:
- Claude: https://platform.claude.com/docs/en/about-claude/pricing
- OpenAI: https://developers.openai.com/api/docs/pricing (Standard, короткий контекст).
Модели, которых нет в таблицах, цены не получают: вызывающий код пишет «нет цены», а не подставляет похожую.
GPT-5.6 Sol: акционная цена действует до 30.11.2026 (по странице OpenAI).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MILLION = 1_000_000.0


@dataclass(frozen=True)
class ClaudePrice:
    """Доллары за миллион токенов. Пара (`above_tokens`, `above`) - цена для длинных запросов (у Haiku 5.5)."""

    input: float
    write_5m: float
    write_1h: float
    read: float
    output: float
    above_tokens: int = 0
    above: "ClaudePrice | None" = None


@dataclass(frozen=True)
class CodexPrice:
    """Доллары за миллион токенов; `write` - запись кэша на 30 минут, если OpenAI ее продает."""

    input: float
    cached: float
    output: float
    write: float | None = None
    long_input: float | None = None
    long_cached: float | None = None
    long_output: float | None = None
    long_write: float | None = None


LONG_CONTEXT_TOKENS = 272_000

# Ключ - название модели без префикса `claude-` и без даты в конце (claude-opus-4-5-20251101 -> opus-4-5).
CLAUDE_PRICES: dict[str, ClaudePrice] = {
    "fable-5-2": ClaudePrice(8, 10, 16, 0.20, 40),
    "mythos-5-2": ClaudePrice(8, 10, 16, 0.20, 40),
    "fable-5-1": ClaudePrice(10, 12.5, 20, 0.25, 50),
    "mythos-5-1": ClaudePrice(10, 12.5, 20, 0.25, 50),
    "fable-5": ClaudePrice(10, 12.5, 20, 1, 50),
    "mythos-5": ClaudePrice(10, 12.5, 20, 1, 50),
    "opus-5-5": ClaudePrice(4, 5, 8, 0.20, 20),
    "opus-5": ClaudePrice(5, 6.25, 10, 0.50, 25),
    "opus-4-8": ClaudePrice(5, 6.25, 10, 0.50, 25),
    "opus-4-7": ClaudePrice(5, 6.25, 10, 0.50, 25),
    "opus-4-6": ClaudePrice(5, 6.25, 10, 0.50, 25),
    "opus-4-5": ClaudePrice(5, 6.25, 10, 0.50, 25),
    "opus-4-1": ClaudePrice(15, 18.75, 30, 1.50, 75),
    "opus-4": ClaudePrice(15, 18.75, 30, 1.50, 75),
    "sonnet-5-5": ClaudePrice(2, 2.5, 4, 0.10, 10),
    "sonnet-5": ClaudePrice(2, 2.5, 4, 0.20, 10),
    "sonnet-4-6": ClaudePrice(3, 3.75, 6, 0.30, 15),
    "sonnet-4-5": ClaudePrice(3, 3.75, 6, 0.30, 15),
    "sonnet-4": ClaudePrice(3, 3.75, 6, 0.30, 15),
    "haiku-5-5": ClaudePrice(
        0.10, 0.125, 0.20, 0.01, 0.50,
        above_tokens=100_000, above=ClaudePrice(0.50, 0.625, 1, 0.05, 2.50),
    ),
    "haiku-4-5": ClaudePrice(1, 1.25, 2, 0.10, 5),
}

CODEX_PRICES: dict[str, CodexPrice] = {
    "gpt-6-astra": CodexPrice(10, 1, 50, 12.5),
    "gpt-6.1-sol": CodexPrice(2, 0.10, 10, 2.5),
    "gpt-6.1-terra": CodexPrice(1, 0.05, 5, 1.25),
    "gpt-6-sol": CodexPrice(2, 0.20, 10, 2.5),
    "gpt-6-luna": CodexPrice(0.10, 0.01, 0.50, 0.125),
    "gpt-5.6-sol": CodexPrice(4, 0.40, 20, 5, 8, 0.80, 30, 10),
    "gpt-5.6-terra": CodexPrice(2, 0.20, 12, 2.5, 4, 0.40, 18, 5),
    "gpt-5.6-luna": CodexPrice(0.20, 0.02, 1.20, 0.25, 0.40, 0.04, 1.80, 0.50),
    "gpt-5.5": CodexPrice(5, 0.50, 30, None, 10, 1, 45),
    "gpt-5.4": CodexPrice(2.5, 0.25, 15, None, 5, 0.50, 22.5),
    "gpt-5.4-mini": CodexPrice(0.75, 0.075, 4.5),
    "gpt-5.4-nano": CodexPrice(0.20, 0.02, 1.25),
    "gpt-5.3-codex": CodexPrice(1.75, 0.175, 14),
    "gpt-5.2": CodexPrice(1.75, 0.175, 14),
    "gpt-5.1": CodexPrice(1.25, 0.125, 10),
    "gpt-5": CodexPrice(1.25, 0.125, 10),
    "gpt-5-mini": CodexPrice(0.25, 0.025, 2),
    "gpt-5-nano": CodexPrice(0.05, 0.005, 0.40),
}

_DATE_SUFFIX = re.compile(r"-(?:\d{8}|latest)$")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def claude_price(model: str) -> ClaudePrice | None:
    """Цена модели Claude по названию из журнала или None, если модели нет в таблице."""
    key = _DATE_SUFFIX.sub("", str(model or "").casefold().strip())
    if key.startswith("claude-"):
        key = key[len("claude-"):]
    return CLAUDE_PRICES.get(key)


def codex_price(model: str) -> CodexPrice | None:
    key = _DATE_SUFFIX.sub("", str(model or "").casefold().strip())
    return CODEX_PRICES.get(key)


def claude_usage_parts(usage: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Токены ответа Claude: (вход, запись кэша 5 минут, запись кэша 1 час, чтение кэша, выход).

    Если в журнале нет разбивки по ярусам, вся запись считается записью на 5 минут.
    """
    created = usage.get("cache_creation")
    write_5m = write_1h = 0
    if isinstance(created, dict):
        write_5m = _int(created.get("ephemeral_5m_input_tokens"))
        write_1h = _int(created.get("ephemeral_1h_input_tokens"))
    else:
        write_5m = _int(usage.get("cache_creation_input_tokens"))
    return _int(usage.get("input_tokens")), write_5m, write_1h, _int(usage.get("cache_read_input_tokens")), _int(usage.get("output_tokens"))


def claude_context_tokens(usage: dict[str, Any]) -> int:
    """Размер контекста, который прочитан или записан в кэш этим ответом."""
    inp, w5, w1, read, _out = claude_usage_parts(usage)
    return inp + w5 + w1 + read


def _claude_tier(price: ClaudePrice, usage: dict[str, Any]) -> ClaudePrice:
    if price.above is not None and claude_context_tokens(usage) > price.above_tokens:
        return price.above
    return price


def claude_cost(model: str, usage: dict[str, Any]) -> float | None:
    """Стоимость одного ответа Claude по ценам API; None, когда у модели нет цены."""
    base = claude_price(model)
    if base is None:
        return None
    price = _claude_tier(base, usage)
    inp, w5, w1, read, out = claude_usage_parts(usage)
    return (inp * price.input + w5 * price.write_5m + w1 * price.write_1h + read * price.read + out * price.output) / MILLION


def claude_rewrite_cost(model: str, usage: dict[str, Any]) -> float | None:
    """Во что обойдется запись контекста в кэш заново после остывания: контекст по цене записи того яруса, в который кэш писался."""
    base = claude_price(model)
    if base is None:
        return None
    context = claude_context_tokens(usage)
    if context <= 0:
        return None
    price = _claude_tier(base, usage)
    _inp, w5, w1, _read, _out = claude_usage_parts(usage)
    rate = price.write_1h if w1 > w5 else price.write_5m
    return context * rate / MILLION


def codex_usage_parts(usage: dict[str, Any]) -> tuple[int, int, int, int]:
    """Токены ответа Codex: (вход без кэша, чтение кэша, запись кэша, выход).

    `input_tokens` у OpenAI включает кэшированные токены, `output_tokens` включает рассуждения.
    """
    total_input = _int(usage.get("input_tokens"))
    cached = min(_int(usage.get("cached_input_tokens")), total_input)
    written = min(_int(usage.get("cache_write_input_tokens")), max(0, total_input - cached))
    return total_input - cached - written, cached, written, _int(usage.get("output_tokens"))


def _codex_rates(price: CodexPrice, total_input: int) -> tuple[float, float, float | None, float]:
    if price.long_input is not None and total_input > LONG_CONTEXT_TOKENS:
        return (
            price.long_input,
            price.long_cached if price.long_cached is not None else price.cached,
            price.long_write if price.long_write is not None else price.write,
            price.long_output if price.long_output is not None else price.output,
        )
    return price.input, price.cached, price.write, price.output


def codex_cost(model: str, usage: dict[str, Any]) -> float | None:
    base = codex_price(model)
    if base is None:
        return None
    total_input = _int(usage.get("input_tokens"))
    inp_rate, cached_rate, write_rate, out_rate = _codex_rates(base, total_input)
    fresh, cached, written, out = codex_usage_parts(usage)
    write_rate = inp_rate if write_rate is None else write_rate
    return (fresh * inp_rate + cached * cached_rate + written * write_rate + out * out_rate) / MILLION


def codex_rewrite_cost(model: str, usage: dict[str, Any]) -> float | None:
    """Во что обойдется обработка контекста заново после остывания: контекст по цене записи кэша, а если OpenAI запись не продает, по цене входа."""
    base = codex_price(model)
    if base is None:
        return None
    total_input = _int(usage.get("input_tokens"))
    if total_input <= 0:
        return None
    inp_rate, _cached_rate, write_rate, _out_rate = _codex_rates(base, total_input)
    return total_input * (inp_rate if write_rate is None else write_rate) / MILLION


def format_money(value: float) -> str:
    """Доллары для карточки: $3,40; суммы меньше цента показываются как <$0,01."""
    if value <= 0:
        return "$0,00"
    if value < 0.005:
        return "<$0,01"
    return "$" + f"{value:.2f}".replace(".", ",")
