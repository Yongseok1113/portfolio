"""kaggle 6-phase 워크플로 스켈레톤 테스트."""

from adp_ma.pipeline.workflows import kaggle_phases


def test_six_phases_in_autokaggle_order():
    phases = kaggle_phases("test goal")
    assert [p.name for p in phases] == [
        "background_understanding",
        "preliminary_eda",
        "data_cleaning",
        "in_depth_eda",
        "feature_engineering",
        "modeling",
    ]


def test_phase_kinds():
    kinds = {p.name: p.kind for p in kaggle_phases("g")}
    assert kinds["background_understanding"] == "analysis"
    assert kinds["preliminary_eda"] == "analysis"
    assert kinds["data_cleaning"] == "transform"
    assert kinds["in_depth_eda"] == "analysis"
    assert kinds["feature_engineering"] == "transform"
    assert kinds["modeling"] == "skip"  # M3에서 구현


def test_goal_embedded_in_transform_objectives():
    phases = {p.name: p for p in kaggle_phases("월별 매출 집계")}
    assert "월별 매출 집계" in phases["data_cleaning"].objective
    assert "월별 매출 집계" in phases["feature_engineering"].objective
