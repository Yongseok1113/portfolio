"""Orchestrator 메타-에이전트 (논문 Stage 2·3).

목표와 데이터 프로파일을 받아 단계(phase) 계획을 세우고,
자기-비평 1회로 계획을 다듬는다. plan-level 백트래킹 시
누적 오류 증거(error_evidence)를 받아 재계획한다.
"""

from pydantic import BaseModel

from adp_ma.llm import LLMClient

_PLAN_SYSTEM = """\
You are the Orchestrator of an autonomous data processing system.
Decompose the user's goal into 2-5 ordered phases of a pandas data pipeline.
Each phase must have a clear objective achievable by generated pandas code
operating on a single DataFrame (no file I/O, no network).
Plans must not lose data unnecessarily: cleaning phases repair or coerce
invalid values instead of dropping rows, unless the goal explicitly says to drop.
Return JSON: {"phases": [{"name": str, "objective": str, "rationale": str}]}"""

_CRITIQUE_SYSTEM = """\
You are reviewing a data pipeline plan. Check for: missing cleaning steps,
wrong ordering, phases that cannot be done with pandas on one DataFrame,
and redundant phases. Return the improved plan as JSON:
{"phases": [{"name": str, "objective": str, "rationale": str}]}
If the plan is already good, return it unchanged."""


class Phase(BaseModel):
    name: str
    objective: str
    rationale: str = ""


class Orchestrator:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def plan(self, goal: str, profile_text: str, error_evidence: str = "") -> list[Phase]:
        user = f"## Goal\n{goal}\n\n## Data profile\n{profile_text}"
        if error_evidence:
            user += (
                "\n\n## Previous attempt failed — accumulated error evidence\n"
                f"{error_evidence}\n"
                "Produce a DIFFERENT plan that avoids these failures."
            )
        data = self.llm.chat_json(_PLAN_SYSTEM, user)
        return _parse_phases(data)

    def critique(self, goal: str, profile_text: str, phases: list[Phase]) -> list[Phase]:
        plan_json = [p.model_dump() for p in phases]
        user = (
            f"## Goal\n{goal}\n\n## Data profile\n{profile_text}\n\n"
            f"## Current plan\n{plan_json}"
        )
        try:
            data = self.llm.chat_json(_CRITIQUE_SYSTEM, user)
            revised = _parse_phases(data)
            return revised or phases
        except (ValueError, KeyError):
            # 비평 단계 실패는 치명적이지 않다 — 원 계획 유지
            return phases


def _parse_phases(data) -> list[Phase]:
    if isinstance(data, dict):
        data = data.get("phases", [])
    phases = []
    for item in data:
        if isinstance(item, dict) and item.get("name") and item.get("objective"):
            phases.append(
                Phase(
                    name=str(item["name"]),
                    objective=str(item["objective"]),
                    rationale=str(item.get("rationale", "")),
                )
            )
    if not phases:
        raise ValueError(f"phase 파싱 실패: {data!r}")
    return phases
