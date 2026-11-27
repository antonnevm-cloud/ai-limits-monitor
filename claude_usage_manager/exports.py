"""Экспорт результата анализа в HTML, JSON, CSV (UTF-8 BOM) и XLSX."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .models import AnalysisResult
from .utils import (
    APP_DIR_NAME,
    display_model,
    estimate_project_weekly_pp,
    format_cost,
    format_percent,
    format_pp,
    format_tokens,
    local_dt_text,
)
from . import APP_VERSION


def export(result: AnalysisResult, path: Path, fmt: str) -> Path:
    fmt = fmt.lower()
    if fmt == "html":
        return export_html(result, path)
    if fmt == "json":
        return export_json(result, path)
    if fmt == "csv":
        return export_csv(result, path)
    if fmt == "xlsx":
        return export_xlsx(result, path)
    raise ValueError(f"Неизвестный формат экспорта: {fmt}")


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def _result_to_dict(result: AnalysisResult) -> dict:
    return {
        "app": APP_DIR_NAME,
        "version": APP_VERSION,
        "agent": result.agent,
        "generated_at": result.generated_at.isoformat() if result.generated_at else None,
        "period": {
            "key": result.period.key,
            "label": result.period.label,
            "start": result.period.start_local.isoformat(),
            "end": result.period.end_local.isoformat(),
        },
        "totals": {
            "tokens": result.total_tokens,
            "cost_usd_equivalent": round(result.total_cost, 6),
            "work_threads": result.work_threads,
            "weekly_consumed_pp": result.weekly_consumed_pp,
        },
        "quota": {
            "provider": result.quota.provider or result.agent,
            "source": result.quota.source,
            "login_method": result.quota.login_method,
            "main_window_id": result.quota.main_window_id,
            "main_percent": result.quota.weekly,
            "five_hour_percent": result.quota.five_hour,
            "weekly_percent": result.quota.weekly,
            "fable_weekly_percent": result.quota.fable_weekly,
            "consumed_pp_by_window": result.quota_consumed_by_window,
            "windows": [
                {
                    "id": window.id, "title": window.title, "used_percent": window.used_percent,
                    "resets_at": window.resets_at.isoformat() if window.resets_at else None,
                    "window_minutes": window.window_minutes, "role": window.role,
                    "informational": window.informational,
                }
                for window in result.quota.windows
            ],
            "legacy": {
                "five_hour_percent": result.quota.five_hour,
                "weekly_percent": result.quota.weekly,
                "fable_weekly_percent": result.quota.fable_weekly,
            },
        },
        "threads": [
            {
                "title": t.title,
                "project": t.project,
                "session_id": t.session_id,
                "is_group": t.is_group,
                "member_ids": t.member_ids,
                "models": t.models,
                "tokens": t.total_tokens,
                "cost_usd_equivalent": round(t.cost, 6),
                "share_percent": round(t.share, 4),
                "estimated_limit_pp": t.weekly_pp,
                "limit_pp_text": t.limit_pp_text,
                "quota_pp_by_window": t.quota_pp_by_window,
                "subagents": t.subagents,
                "service": t.is_service,
                "pinned": t.pinned,
                "hidden": t.hidden,
                "first_activity": t.first_activity.isoformat() if t.first_activity else None,
                "last_activity": t.last_activity.isoformat() if t.last_activity else None,
            }
            for t in result.threads
        ],
        "projects": [
            {"project": p.project, "threads": p.threads, "tokens": p.total_tokens,
             "cost_usd_equivalent": round(p.cost, 6), "share_percent": round(p.share, 4),
             "estimated_limit_pp": estimate_project_weekly_pp(p.share, result.weekly_consumed_pp),
             "limit_pp_text": p.limit_pp_text, "quota_pp_by_window": p.quota_pp_by_window}
            for p in result.projects
        ],
        "models": [
            {"model": m.model, "input_tokens": m.input_tokens, "output_tokens": m.output_tokens,
             "cache_creation_tokens": m.cache_creation, "cache_read_tokens": m.cache_read,
             "tokens": m.total_tokens, "cost_usd_equivalent": round(m.cost, 6),
             "share_percent": round(m.share, 4), "quota_percent": m.quota_percent,
             "quota_pp": m.quota_pp, "quota_window_id": m.quota_window_id,
             "quota_reset": m.quota_reset.isoformat() if m.quota_reset else None}
            for m in result.model_rows
        ],
        "timeline": [
            {"label": b.label, "start": b.start.isoformat(), "tokens": b.total_tokens,
             "estimated_cost_usd": round(b.estimated_cost, 6)}
            for b in result.timeline
        ],
        "notes": [
            f"Агент: {result.agent}.",
            "Стоимость — условный эквивалент локального источника, не является оплатой.",
            "Стоимость в разбивке по времени распределена пропорционально токенам (оценка).",
            "Тексты переписки не сохраняются.",
        ],
    }


def export_json(result: AnalysisResult, path: Path) -> Path:
    path.write_text(
        json.dumps(_result_to_dict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# CSV (UTF-8 BOM) — треды текущего периода
# --------------------------------------------------------------------------- #
def export_csv(result: AnalysisResult, path: Path) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow([
            "Агент", "Название", "Проект", "Модели", "Токены", "Стоимость (усл.)",
            "Доля %", "Лимит, п.п. (оценка)", "Субагенты",
            "Начало", "Конец", "Служебный", "Закреплён", "Session ID",
        ])
        for t in result.threads:
            writer.writerow([
                result.agent, t.title, t.project, ", ".join(display_model(m) for m in t.models),
                t.total_tokens, round(t.cost, 4), round(t.share, 3),
                t.limit_pp_text or ("" if t.weekly_pp is None else round(t.weekly_pp, 3)),
                t.subagents,
                local_dt_text(t.first_activity), local_dt_text(t.last_activity),
                "да" if t.is_service else "", "да" if t.pinned else "",
                t.session_id,
            ])
    return path


# --------------------------------------------------------------------------- #
# XLSX — листы Summary, Threads, Projects, Timeline, Models
# --------------------------------------------------------------------------- #
def export_xlsx(result: AnalysisResult, path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2C3442")
    money_fmt = "#,##0.00"
    int_fmt = "#,##0"
    pct_fmt = "0.00"

    def style_header(ws, columns: int) -> None:
        for col in range(1, columns + 1):
            cell = ws.cell(row=1, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.freeze_panes = "A2"

    def autosize(ws) -> None:
        for column_cells in ws.columns:
            length = 0
            letter = get_column_letter(column_cells[0].column)
            for cell in column_cells:
                value = "" if cell.value is None else str(cell.value)
                length = max(length, len(value))
            ws.column_dimensions[letter].width = min(60, max(10, length + 2))

    # --- Summary ---
    ws = wb.active
    ws.title = "Summary"
    period = result.period
    rows = [
        ("Показатель", "Значение"),
        ("Период", f"{period.label}: {local_dt_text(period.start_local)} — {local_dt_text(period.end_local)}"),
        ("Сформировано", local_dt_text(result.generated_at)),
        ("Токены", result.total_tokens),
        ("Условная стоимость, $", round(result.total_cost, 2)),
        ("Рабочих тредов", result.work_threads),
        (result.quota.main_label or "Основной лимит", result.quota.weekly if result.quota.weekly is not None else "нет данных"),
        ("Расход основного лимита, п.п.", "нет данных" if result.weekly_consumed_pp is None else round(result.weekly_consumed_pp, 2)),
    ]
    for window in result.quota.windows:
        rows.append((f"Лимит · {window.title}, %", window.used_percent if window.used_percent is not None else "нет данных"))
        if window.resets_at:
            rows.append((f"Сброс · {window.title}", local_dt_text(window.resets_at)))
    for row in rows:
        ws.append(row)
    style_header(ws, 2)
    autosize(ws)

    # --- Threads ---
    ws = wb.create_sheet("Threads")
    ws.append([
        "Название", "Проект", "Модели", "Токены", "Стоимость (усл.)", "Доля %",
        "Лимит, п.п. (оценка)", "Субагенты", "Начало", "Конец", "Session ID",
    ])
    for t in result.threads:
        ws.append([
            t.title, t.project, ", ".join(display_model(m) for m in t.models),
            t.total_tokens, round(t.cost, 4), round(t.share, 3),
            t.limit_pp_text or (None if t.weekly_pp is None else round(t.weekly_pp, 3)), t.subagents,
            local_dt_text(t.first_activity), local_dt_text(t.last_activity), t.session_id,
        ])
    _apply_formats(ws, {4: int_fmt, 5: money_fmt, 6: pct_fmt, 7: pct_fmt, 8: int_fmt})
    style_header(ws, 11)
    autosize(ws)

    # --- Projects ---
    ws = wb.create_sheet("Projects")
    ws.append(["Проект", "Тредов", "Токены", "Стоимость (усл.)", "Доля %", "Лимит, п.п. (оценка)"])
    for p in result.projects:
        ws.append([p.project, p.threads, p.total_tokens, round(p.cost, 4), round(p.share, 3),
                   p.limit_pp_text or estimate_project_weekly_pp(p.share, result.weekly_consumed_pp)])
    _apply_formats(ws, {2: int_fmt, 3: int_fmt, 4: money_fmt, 5: pct_fmt, 6: pct_fmt})
    style_header(ws, 6)
    autosize(ws)

    # --- Timeline ---
    ws = wb.create_sheet("Timeline")
    ws.append(["Интервал", "Токены", "Стоимость (оценка, усл.)"])
    for b in result.timeline:
        ws.append([b.label, b.total_tokens, round(b.estimated_cost, 4)])
    _apply_formats(ws, {2: int_fmt, 3: money_fmt})
    style_header(ws, 3)
    autosize(ws)

    # --- Models ---
    ws = wb.create_sheet("Models")
    ws.append(["Модель", "Ввод", "Вывод", "Кэш (создание)", "Кэш (чтение)", "Токены", "Стоимость (усл.)", "Доля %", "Лимит %", "П.п. периода", "Сброс"])
    for m in result.model_rows:
        ws.append([
            display_model(m.model), m.input_tokens, m.output_tokens, m.cache_creation,
            m.cache_read, m.total_tokens, round(m.cost, 4), round(m.share, 3),
            m.quota_percent, m.quota_pp, local_dt_text(m.quota_reset),
        ])
    _apply_formats(ws, {2: int_fmt, 3: int_fmt, 4: int_fmt, 5: int_fmt, 6: int_fmt, 7: money_fmt, 8: pct_fmt, 9: pct_fmt, 10: pct_fmt})
    style_header(ws, 11)
    autosize(ws)

    wb.save(path)
    return path


def _apply_formats(ws, formats: dict[int, str]) -> None:
    for row in ws.iter_rows(min_row=2):
        for col, fmt in formats.items():
            cell = row[col - 1]
            if isinstance(cell.value, (int, float)):
                cell.number_format = fmt


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #
def export_html(result: AnalysisResult, path: Path) -> Path:
    period = result.period
    q = result.quota

    def esc(value: str) -> str:
        return html.escape(str(value))

    def thread_rows() -> str:
        out = []
        for index, t in enumerate(result.threads, 1):
            badge = f'<span class="badge">+{t.subagents} субаг.</span>' if t.subagents else ""
            group = '<span class="badge grp">группа</span>' if t.is_group else ""
            pin = '<span class="badge pin">закреплён</span>' if t.pinned else ""
            models = ", ".join(display_model(m) for m in t.models) or "—"
            out.append(f"""
<tr>
<td class="rank">{index}</td>
<td class="thread"><div class="thread-title">{esc(t.title)} {badge}{group}{pin}</div>
<div class="muted">{esc(t.project)}</div></td>
<td>{esc(models)}</td>
<td class="num">{format_tokens(t.total_tokens)}</td>
<td class="num strong">{format_cost(t.cost)}</td>
<td class="share-cell"><div class="share-line">{format_percent(t.share)}</div>
<div class="bar"><i style="width:{min(100.0, t.share):.3f}%"></i></div></td>
<td class="num">{esc(t.limit_pp_text) if t.limit_pp_text else format_pp(t.weekly_pp)}</td>
</tr>""")
        return "\n".join(out)

    def project_rows() -> str:
        return "\n".join(
            f"<tr><td>{esc(p.project)}</td><td class='num'>{p.threads}</td>"
            f"<td class='num'>{format_tokens(p.total_tokens)}</td>"
            f"<td class='num strong'>{format_cost(p.cost)}</td>"
            f"<td class='num'>{format_percent(p.share)}</td>"
            f"<td class='num'>{esc(p.limit_pp_text) if p.limit_pp_text else format_pp(estimate_project_weekly_pp(p.share, result.weekly_consumed_pp))}</td></tr>"
            for p in result.projects
        )

    def model_rows() -> str:
        return "\n".join(
            f"<tr><td>{esc(display_model(m.model))}</td>"
            f"<td class='num'>{format_tokens(m.total_tokens)}</td>"
            f"<td class='num strong'>{format_cost(m.cost)}</td>"
            f"<td class='num'>{format_percent(m.share)}</td>"
            f"<td class='num'>{format_percent(m.quota_percent, 2) if m.quota_percent is not None else '—'}</td>"
            f"<td class='num'>{format_pp(m.quota_pp)}</td>"
            f"<td class='num'>{esc(local_dt_text(m.quota_reset))}</td></tr>"
            for m in result.model_rows
        )

    def timeline_rows() -> str:
        return "\n".join(
            f"<tr><td>{esc(b.label)}</td><td class='num'>{format_tokens(b.total_tokens)}</td>"
            f"<td class='num'>{format_cost(b.estimated_cost)} <span class='muted'>оц.</span></td></tr>"
            for b in result.timeline
        )

    weekly = format_percent(q.weekly, 2 if result.agent == "antigravity" else 0) if q.weekly is not None else "нет данных"
    if result.agent == "claude":
        secondary_label = "Пятичасовое окно"
        secondary_value = format_percent(q.five_hour, 0) if q.five_hour is not None else "нет данных"
        secondary_note = "текущее значение"
    elif result.agent == "codex":
        secondary_label = q.extra_label or "Codex Spark"
        secondary_value = format_percent(q.fable_weekly, 0) if q.fable_weekly is not None else "нет данных"
        secondary_note = "дополнительный недельный пул" if q.fable_weekly is not None else "нет данных"
    elif result.agent == "grok":
        pools = [window for window in q.windows if window.role == "group"]
        resets = [window.resets_at for window in pools if window.resets_at]
        secondary_label = "Сброс цикла"
        secondary_value = local_dt_text(min(resets)) if resets else "нет данных"
        secondary_note = "Grok · общий недельный лимит"
    else:
        quota_pools = [window for window in q.windows if window.role in {"group", "model"}]
        secondary_label = "Пулов квоты"
        secondary_value = str(len(quota_pools)) if quota_pools else "нет данных"
        secondary_note = "группы и модели" if quota_pools else "лимиты во вкладке «Модели»"
    consumed = format_pp(result.weekly_consumed_pp)
    period_text = f"{esc(period.label)}: {local_dt_text(period.start_local)} — {local_dt_text(period.end_local)}"

    document = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Usage Manager — отчёт</title>
<style>
:root{{color-scheme:light dark;--bg:#0e1117;--panel:#171b23;--panel2:#1d2330;--text:#eef2f7;
--muted:#9aa6b5;--line:#2c3442;--accent:#d7834f;--accent2:#f0a36f;--shadow:0 14px 40px rgba(0,0,0,.22)}}
*{{box-sizing:border-box}}body{{margin:0;font-family:Segoe UI,Inter,Arial,sans-serif;background:var(--bg);color:var(--text)}}
main{{max-width:1500px;margin:0 auto;padding:28px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;margin-bottom:22px}}
h1{{margin:0 0 7px;font-size:30px}}.subtitle,.muted,.section-note{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:14px;margin-bottom:18px}}
.card,.panel{{background:linear-gradient(145deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}}
.card{{padding:17px 18px}}.card-label{{color:var(--muted);font-size:13px;margin-bottom:8px}}
.card-value{{font-size:26px;font-weight:750}}.card-small{{color:var(--muted);font-size:12px;margin-top:6px}}
.panel{{padding:18px;margin-top:18px}}h2{{margin:0 0 6px;font-size:20px}}.table-wrap{{overflow:auto;margin-top:14px}}
table{{width:100%;border-collapse:collapse}}th{{color:var(--muted);font-size:12px;text-transform:uppercase;text-align:left;padding:11px 10px;border-bottom:1px solid var(--line)}}
td{{padding:12px 10px;border-bottom:1px solid var(--line);vertical-align:middle}}tbody tr:hover{{background:rgba(255,255,255,.025)}}
.rank{{color:var(--muted);width:36px}}.thread{{min-width:280px;max-width:520px}}.thread-title{{font-weight:700;line-height:1.35}}
.num{{text-align:right;white-space:nowrap}}.strong{{font-weight:750}}.share-cell{{min-width:145px}}.share-line{{text-align:right;margin-bottom:6px}}
.bar{{height:6px;background:#29303d;border-radius:9px;overflow:hidden}}.bar i{{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2))}}
.badge{{display:inline-block;margin-left:6px;padding:2px 6px;border-radius:8px;background:#343c4c;color:#cfd7e3;font-size:11px}}
.badge.grp{{background:#33455c}}.badge.pin{{background:#4c3a2a}}
footer{{color:var(--muted);font-size:12px;margin-top:20px;text-align:center}}
@media(prefers-color-scheme:light){{:root{{--bg:#f3f5f8;--panel:#fff;--panel2:#f7f8fa;--text:#1c2430;--muted:#657183;--line:#dfe4ea;--shadow:0 10px 28px rgba(31,42,55,.09)}}.bar{{background:#e8ebef}}}}
</style></head><body><main>
<header><div><h1>AI Usage · {esc(result.agent)} · Claude Usage Manager</h1><div class="subtitle">{period_text}. Локальные данные выбранного AI-агента.</div></div>
<div class="muted">Сформировано {local_dt_text(result.generated_at)}<br>Версия {APP_VERSION}</div></header>
<section class="cards">
<div class="card"><div class="card-label">Токены</div><div class="card-value">{format_tokens(result.total_tokens)}</div><div class="card-small">{result.work_threads} рабочих тредов</div></div>
<div class="card"><div class="card-label">Условная стоимость</div><div class="card-value">{format_cost(result.total_cost)}</div><div class="card-small">не является оплатой</div></div>
<div class="card"><div class="card-label">{esc(q.main_label or "Основной лимит")}</div><div class="card-value">{weekly}</div><div class="card-small">{esc(q.login_method or q.source)}</div></div>
<div class="card"><div class="card-label">{esc(secondary_label)}</div><div class="card-value">{esc(secondary_value)}</div><div class="card-small">{esc(secondary_note)}</div></div>
<div class="card"><div class="card-label">Расход недели</div><div class="card-value">{consumed}</div><div class="card-small">оценка по истории</div></div>
</section>
<section class="panel"><h2>Треды</h2><p class="section-note">Закреплённые сверху. Отсортированы по условному расходу.</p>
<div class="table-wrap"><table><thead><tr><th>#</th><th>Тред и проект</th><th>Модели</th><th>Токены</th><th>Стоимость</th><th>Доля</th><th>Недельные п.п.</th></tr></thead>
<tbody>{thread_rows()}</tbody></table></div></section>
<section class="panel"><h2>Проекты</h2><div class="table-wrap"><table>
<thead><tr><th>Проект</th><th>Тредов</th><th>Токены</th><th>Стоимость</th><th>Доля</th><th>Нед. п.п.</th></tr></thead>
<tbody>{project_rows()}</tbody></table></div></section>
<section class="panel"><h2>Модели</h2><div class="table-wrap"><table>
<thead><tr><th>Модель</th><th>Токены</th><th>Стоимость</th><th>Доля</th><th>Лимит %</th><th>П.п. периода</th><th>Сброс</th></tr></thead>
<tbody>{model_rows()}</tbody></table></div></section>
<section class="panel"><h2>По времени</h2><p class="section-note">Токены точные, стоимость — оценка (распределена по токенам).</p>
<div class="table-wrap"><table><thead><tr><th>Интервал</th><th>Токены</th><th>Стоимость</th></tr></thead>
<tbody>{timeline_rows()}</tbody></table></div></section>
<footer>Всё обрабатывается локально. Тексты переписки не сохраняются.</footer>
</main></body></html>"""

    path.write_text(document, encoding="utf-8")
    return path
