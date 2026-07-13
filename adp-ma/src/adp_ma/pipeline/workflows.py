"""고정 워크플로 스켈레톤 (AutoKaggle §Six Sequential Competition Phases).

dynamic 워크플로(Orchestrator가 phase를 계획)와 달리, kaggle 워크플로는
6-phase 뼈대를 고정하고 phase 내부의 태스크 분해(≤4)만 LLM에 맡긴다.

phase.kind:
  analysis  — 데이터를 바꾸지 않고 LLM이 인사이트만 생산 (이후 phase의 컨텍스트)
  transform — 기존 실행 경로 (expand → tool plan/codegen → 검증)
  skip      — 아직 미구현 단계 (기록만 남기고 통과)
"""

from adp_ma.meta_agents.orchestrator import Phase

MODELING_SKIP_NOTE = "모델 학습·검증·제출 — M3에서 구현 예정 (sklearn + submission)"


def kaggle_phases(goal: str) -> list[Phase]:
    return [
        Phase(
            name="background_understanding",
            kind="analysis",
            objective=(
                "Summarize the task: restate the goal in one paragraph, describe what the "
                "data appears to contain, and state what the final output must look like."
            ),
            rationale="모든 후속 phase가 공유할 과제 이해 (M2에서 Reader가 문서 입력까지 확장)",
        ),
        Phase(
            name="preliminary_eda",
            kind="analysis",
            objective=(
                "Identify concrete data quality issues to fix: missing values, duplicates, "
                "inconsistent formats/casing, wrong dtypes, suspicious outliers. "
                "Be specific about which columns are affected."
            ),
            rationale="클리닝 phase의 작업 목록 근거",
        ),
        Phase(
            name="data_cleaning",
            kind="transform",
            objective=(
                "Clean the data based on the EDA findings: handle duplicates, missing values, "
                f"formats and dtypes as needed for the goal: {goal}"
            ),
            rationale="이후 분석·피처링이 신뢰할 수 있는 기반 확보",
        ),
        Phase(
            name="in_depth_eda",
            kind="analysis",
            objective=(
                "Analyze the cleaned data: distributions, relationships and category balance. "
                "Recommend specific feature engineering steps (which columns, which transforms)."
            ),
            rationale="FE phase의 작업 목록 근거",
        ),
        Phase(
            name="feature_engineering",
            kind="transform",
            objective=(
                "Create and transform features per the EDA recommendations "
                f"to serve the goal: {goal}"
            ),
            rationale="모델링(M3)·분석에 유용한 표현 확보",
        ),
        Phase(
            name="modeling",
            kind="skip",
            objective=MODELING_SKIP_NOTE,
            rationale="M3 마일스톤",
        ),
    ]
