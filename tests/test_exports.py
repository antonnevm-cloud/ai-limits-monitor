import datetime as dt
import json

from claude_usage_manager.exports import (
    export_csv, export_html, export_json, export_xlsx,
)
from claude_usage_manager.models import (
    AnalysisResult, ModelRow, Period, ProjectRow, QuotaSnapshot, ThreadRow, TimelineBucket,
)

TZ = dt.timezone(dt.timedelta(hours=3))


def _result():
    now = dt.datetime(2026, 8, 5, 14, 0, tzinfo=TZ)
    period = Period("today", "Сегодня", now.replace(hour=0, minute=0), now)
    thread = ThreadRow(
        session_id="S1", title="Тестовый тред", project="Demo",
        models=["claude-sonnet-5"], cost=1.5, total_tokens=1000, input_tokens=400,
        output_tokens=600, first_activity=now.replace(hour=10),
        last_activity=now.replace(hour=13), subagents=1, share=100.0, weekly_pp=0.5,
    )
    return AnalysisResult(
        period=period,
        threads=[thread],
        projects=[ProjectRow(project="Demo", threads=1, total_tokens=1000, cost=1.5, share=100.0)],
        model_rows=[ModelRow(model="claude-sonnet-5", input_tokens=400, output_tokens=600,
                             total_tokens=1000, cost=1.5, share=100.0)],
        timeline=[TimelineBucket(label="05.08 10:00", start=now.replace(hour=10),
                                 total_tokens=1000, estimated_cost=1.5)],
        total_tokens=1000, total_cost=1.5, work_threads=1,
        quota=QuotaSnapshot(five_hour=66.0, weekly=56.0, fable_weekly=6.0),
        weekly_consumed_pp=0.5, generated_at=now,
    )


def test_export_csv_bom(tmp_path):
    path = tmp_path / "out.csv"
    export_csv(_result(), path)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = raw.decode("utf-8-sig")
    assert "Название" in text and "Тестовый тред" in text


def test_export_xlsx_sheets(tmp_path):
    from openpyxl import load_workbook

    path = tmp_path / "out.xlsx"
    export_xlsx(_result(), path)
    assert path.exists()
    wb = load_workbook(path)
    assert wb.sheetnames == ["Summary", "Threads", "Projects", "Timeline", "Models"]
    assert wb["Threads"].freeze_panes == "A2"


def test_exports_keep_formula_like_titles_as_text(tmp_path):
    # Название треда из журнала не становится формулой Excel и не срывает выгрузку (security#8, analytics#12).
    from openpyxl import load_workbook

    result = _result()
    result.threads[0].title = '=HYPERLINK("http://example.invalid","x")'
    result.threads[0].project = "Demo\x07bell"
    csv_path = tmp_path / "out.csv"
    export_csv(result, csv_path)
    row = csv_path.read_text(encoding="utf-8-sig").splitlines()[1].split(";")
    assert row[1].startswith("\"'=HYPERLINK") or row[1].startswith("'=HYPERLINK")
    assert row[5] == "1,5" and row[6] == "100"
    xlsx_path = tmp_path / "out.xlsx"
    export_xlsx(result, xlsx_path)
    sheet = load_workbook(xlsx_path)["Threads"]
    assert sheet["A2"].data_type == "s"
    assert sheet["A2"].value.startswith("=HYPERLINK")
    assert sheet["B2"].value == "Demobell"


def test_export_json(tmp_path):
    path = tmp_path / "out.json"
    export_json(_result(), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["totals"]["tokens"] == 1000
    assert data["threads"][0]["title"] == "Тестовый тред"
    assert data["quota"]["weekly_percent"] == 56.0


def test_export_html(tmp_path):
    path = tmp_path / "out.html"
    export_html(_result(), path)
    text = path.read_text(encoding="utf-8")
    assert "<!doctype html>" in text
    assert "Тестовый тред" in text


def test_export_html_uses_provider_specific_secondary_quota(tmp_path):
    result = _result()
    result.agent = "codex"
    result.quota = QuotaSnapshot(
        provider="codex",
        main_label="Недельная квота",
        weekly=56.0,
        fable_weekly=6.0,
        extra_label="Codex Spark",
    )
    path = tmp_path / "codex.html"

    export_html(result, path)
    text = path.read_text(encoding="utf-8")

    assert "Codex Spark" in text
    assert "6%" in text
    assert "Пятичасовое окно" not in text
