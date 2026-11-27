from claude_usage_manager.utils import estimate_project_weekly_pp


def test_project_weekly_pp_proportional():
    assert estimate_project_weekly_pp(40.0, 5.0) == 2.0


def test_project_weekly_pp_missing_history():
    assert estimate_project_weekly_pp(40.0, None) is None


def test_project_weekly_pp_projects_sum_to_total():
    shares = [40.49, 22.67, 18.20, 15.31, 2.11, 1.22]
    values = [estimate_project_weekly_pp(share, 5.0) for share in shares]
    assert abs(sum(value for value in values if value is not None) - 5.0) < 1e-9
