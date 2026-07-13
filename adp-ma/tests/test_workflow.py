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
    assert kinds["background_understanding"] == "reader"  # M2: Reader가 담당
    assert kinds["preliminary_eda"] == "analysis"
    assert kinds["data_cleaning"] == "transform"
    assert kinds["in_depth_eda"] == "analysis"
    assert kinds["feature_engineering"] == "transform"
    assert kinds["modeling"] == "skip"  # M3에서 구현


def test_goal_embedded_in_transform_objectives():
    phases = {p.name: p for p in kaggle_phases("월별 매출 집계")}
    assert "월별 매출 집계" in phases["data_cleaning"].objective
    assert "월별 매출 집계" in phases["feature_engineering"].objective


# ── M2: 최종 리포트 조립 (LLM 불필요한 순수 함수) ────────────────────────────
def test_assemble_final_report():
    from adp_ma.meta_agents import assemble_final_report

    md = assemble_final_report(
        goal="월별 매출 집계",
        brief="## Objective\n요약",
        sections=[
            ("preliminary_eda", "- 중복 25행 발견"),
            ("data_cleaning", "- remove_duplicates로 500행 확보"),
        ],
        result_info={"rows": 500, "output": "out.csv"},
    )
    assert md.startswith("# ADP-MA Run Report")
    assert "## Task Brief" in md
    assert "## Phase — data_cleaning" in md
    assert "- rows: 500" in md
    # phase 순서 보존
    assert md.index("preliminary_eda") < md.index("data_cleaning")


def test_assemble_final_report_without_brief():
    from adp_ma.meta_agents import assemble_final_report

    md = assemble_final_report("g", "", [], {"rows": 1})
    assert "## Task Brief" not in md  # brief 없으면 섹션 생략
