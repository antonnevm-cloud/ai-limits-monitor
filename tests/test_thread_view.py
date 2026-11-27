import json

import pytest

from claude_usage_manager.thread_view import CHART_MODES, FIELDS, USAGE_MODES, ThreadView


def test_defaults_enable_everything():
    view = ThreadView()
    assert (view.compact, view.timer, view.title, view.project, view.rewrite, view.warning, view.glutton, view.pin_hide, view.forecast) == (True,) * 9
    assert (view.usage, view.chart) == ("both", "click")
    assert ThreadView.from_json(None) == view and ThreadView.from_json("") == view


def test_json_roundtrip_and_partial_values():
    view = ThreadView(timer=False, project=False, usage="pct", chart="off")
    assert ThreadView.from_json(view.to_json()) == view
    assert json.loads(view.to_json()) == {
        "chart": "off", "compact": True, "font": "0", "forecast": True, "glutton": True, "pin_hide": True, "project": False, "rewrite": True,
        "terminal": False, "timer": False, "title": True, "usage": "pct", "warning": True,
    }
    assert ThreadView.from_json('{"timer": false}') == ThreadView(timer=False)


@pytest.mark.parametrize("old_mode", ["hover", "rclick"])
def test_old_chart_modes_open_on_click_and_save_as_click(old_mode):
    view = ThreadView.from_json(json.dumps({"chart": old_mode, "timer": False, "usage": "pct"}))
    assert view == ThreadView(chart="click", timer=False, usage="pct")
    assert json.loads(view.to_json())["chart"] == "click"
    with pytest.raises(ValueError):
        view.with_value("chart", old_mode)


def test_claude_terminal_threads_are_hidden_by_default():
    assert ThreadView().terminal is False
    assert ThreadView.from_json('{"terminal": true}').terminal is True


def test_stage_one_settings_keep_working():
    old = '{"pin_hide": true, "project": false, "timer": true, "title": true, "warning": false}'
    assert ThreadView.from_json(old) == ThreadView(project=False, warning=False)


@pytest.mark.parametrize("raw", ["abc", "[1, 2]", "42", "null", '{"timer": "no", "title": 0}', "{оборван", '{"usage": "много", "chart": true}', '{"usage": 1}'])
def test_broken_or_foreign_values_fall_back_to_defaults(raw):
    assert ThreadView.from_json(raw) == ThreadView()


def test_unknown_keys_are_ignored_and_with_value_checks_name_and_value():
    assert ThreadView.from_json('{"x": false, "title": false}') == ThreadView(title=False)
    assert ThreadView().with_value("timer", False) == ThreadView(timer=False)
    assert ThreadView().with_value("usage", "usd") == ThreadView(usage="usd")
    with pytest.raises(ValueError):
        ThreadView().with_value("nothing", True)
    with pytest.raises(ValueError):
        ThreadView().with_value("chart", "always")


def test_panel_rows_follow_the_mockup_order():
    assert [spec.name for spec in FIELDS] == [
        "compact", "timer", "title", "project", "usage", "rewrite", "warning", "glutton", "pin_hide", "terminal", "forecast", "chart", "font",
    ]
    assert [spec.label for spec in FIELDS][:5] == ["Компактно", "Время жизни кэша", "Название треда", "Проект и среда", "Расход треда"]
    assert next(spec.label for spec in FIELDS if spec.name == "pin_hide") == "Учитывать правила тредов"
    assert next(spec.label for spec in FIELDS if spec.name == "chart") == "График расхода"
    assert all(hasattr(ThreadView(), spec.name) for spec in FIELDS)
    assert [spec.name for spec in FIELDS if spec.choices] == ["usage", "chart", "font"]
    assert [spec.name for spec in FIELDS if spec.indent] == ["chart"]
    assert [label for _value, label in USAGE_MODES] == ["нет", "% недели", "доллары", "оба"]
    assert CHART_MODES == (("click", "по щелчку"), ("off", "выключен"))
