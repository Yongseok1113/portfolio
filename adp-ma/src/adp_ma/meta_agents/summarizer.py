"""Summarizer 메타-에이전트 (AutoKaggle §Summarizer — M2).

transform phase가 끝날 때마다 실행 이벤트를 짧은 리포트로 압축한다.
- 압축본은 이후 phase의 프롬프트 컨텍스트로 주입 (원시 로그 대비 토큰 절감)
- 전체 리포트(report.md)는 실행 종료 시 조립 — 사람이 읽는 감사 요약
"""

import json

from adp_ma.llm import LLMClient

_SUMMARIZER_SYSTEM = """\
You are the Summarizer of an autonomous data pipeline.
Compress one phase's execution events into 3-5 plain bullet lines:
what changed in the data (rows/columns, with names), which tools or generated
code did it, and any monitor warnings. Under 120 words. No JSON, no code."""


class Summarizer:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def summarize_phase(self, phase_name: str, events: list[dict]) -> str:
        log = json.dumps(events, ensure_ascii=False, default=str)[:6000]
        user = f"## Phase\n{phase_name}\n\n## Execution events\n{log}"
        return self.llm.chat(_SUMMARIZER_SYSTEM, user).strip()


def assemble_final_report(
    goal: str,
    brief: str,
    sections: list[tuple[str, str]],
    result_info: dict,
) -> str:
    """최종 report.md 조립 — LLM 없이 결정적으로 (테스트 가능).

    sections: (phase 이름, 요약/분석 텍스트) 순서 목록.
    """
    parts = ["# ADP-MA Run Report", f"## Goal\n\n{goal}"]
    if brief:
        parts.append(f"## Task Brief\n\n{brief}")
    for name, text in sections:
        parts.append(f"## Phase — {name}\n\n{text}")
    info_lines = "\n".join(f"- {k}: {v}" for k, v in result_info.items())
    parts.append(f"## Result\n\n{info_lines}")
    return "\n\n".join(parts) + "\n"
