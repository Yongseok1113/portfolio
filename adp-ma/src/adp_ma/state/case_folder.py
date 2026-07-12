"""Case folder — 실행 1회의 완전한 감사 추적 (논문 §Communication & State).

runs/<run-id>/
├── decisions.jsonl   # 단계별 결정 레코드 (append-only)
├── profile.json      # 데이터 프로파일
├── plan-<n>.json     # n번째 계획 (plan-level 백트래킹마다 증가)
├── agents/           # 실행된 ground agent 코드
└── output.csv        # 최종 산출물 (성공 시)
"""

import json
from datetime import datetime
from pathlib import Path


class CaseFolder:
    def __init__(self, base_dir: str = "runs", run_id: str | None = None):
        self.run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = Path(base_dir) / self.run_id
        (self.dir / "agents").mkdir(parents=True, exist_ok=True)
        self._decisions = self.dir / "decisions.jsonl"
        self._agent_seq = 0

    def record(self, stage: str, payload: dict):
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "stage": stage, **payload}
        with open(self._decisions, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def save_json(self, name: str, obj):
        (self.dir / name).write_text(
            json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    def save_text(self, name: str, text: str):
        (self.dir / name).write_text(text, encoding="utf-8")

    def save_agent_code(self, name: str, code: str) -> Path:
        self._agent_seq += 1
        path = self.dir / "agents" / f"{self._agent_seq:02d}-{name}.py"
        path.write_text(code, encoding="utf-8")
        return path
