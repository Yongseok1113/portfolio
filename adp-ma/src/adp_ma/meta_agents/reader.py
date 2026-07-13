"""Reader 메타-에이전트 (AutoKaggle §Reader — M2).

과제 문서(대회 overview 상당)와 데이터 프로파일을 읽어 구조화된
task brief를 만든다. brief는 모든 후속 phase의 공통 컨텍스트가 된다.
과제 문서가 없으면 goal + 프로파일만으로 요약한다.
"""

from adp_ma.llm import LLMClient

_READER_SYSTEM = """\
You are the Reader of an autonomous data-science system.
Write a concise task brief in markdown with exactly these sections:
## Objective — the goal restated in one paragraph
## Data — what each column appears to represent (one line per column)
## Evaluation — the stated metric, or how success should be judged if unstated
## Output requirements — the exact deliverable format (columns, granularity)
Base it ONLY on the provided goal, task document and data profile.
Keep it under 350 words. No code."""


class Reader:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def brief(self, goal: str, profile_text: str, task_doc: str = "") -> str:
        user = f"## Goal\n{goal}\n\n## Data profile\n{profile_text}"
        if task_doc:
            user += f"\n\n## Task document\n{task_doc[:8000]}"
        return self.llm.chat(_READER_SYSTEM, user).strip()
