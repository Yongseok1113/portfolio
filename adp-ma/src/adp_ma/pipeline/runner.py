"""파이프라인 러너 — 논문의 6단계 사이클과 2단계 백트래킹을 조립한다.

1 Data Understanding → 2 High-Level Planning → 3 Critique →
4 Phase Expansion → 5 Ground-Agent Execution (progressive sampling) →
6 Finalization

phase-level 백트래킹: 직전 성공 스냅샷으로 되돌리고 Architect가 재확장.
plan-level 백트래킹: phase 실패 누적 시 오류 증거와 함께 전체 재계획.
"""

from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from adp_ma.config import Settings
from adp_ma.contracts import sanitize_contract, verify_contract
from adp_ma.ground import run_ladder
from adp_ma.llm import LLMClient
from adp_ma.meta_agents import (
    Architect,
    Monitor,
    Orchestrator,
    Phase,
    StepMetrics,
    Verdict,
)
from adp_ma.profiling import overall_null_rate, profile_dataframe, profile_to_prompt
from adp_ma.state import CaseFolder


class PipelineAbort(Exception):
    """Monitor가 abort 판정을 내렸을 때 실행 전체를 중단한다."""


class PipelineResult(BaseModel):
    ok: bool
    message: str = ""
    run_dir: str = ""
    output_path: str | None = None
    phases_completed: int = 0
    plan_retries: int = 0
    llm_calls: int = 0
    total_tokens: int = 0


class PipelineRunner:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.llm = LLMClient(self.settings)
        self.orchestrator = Orchestrator(self.llm)
        self.architect = Architect(self.llm)
        self.monitor = Monitor()

    def run(self, input_path: str | Path, goal: str, output_path: str | Path | None = None) -> PipelineResult:
        case = CaseFolder(self.settings.runs_dir)
        df = _read_table(Path(input_path))

        # Stage 1 — Data Understanding
        profile = profile_dataframe(df)
        case.save_json("profile.json", profile)
        case.record("data_understanding", {"n_rows": profile["n_rows"], "n_cols": profile["n_cols"]})
        profile_text = profile_to_prompt(profile)

        plan_retries = 0
        error_evidence = ""
        try:
            while True:
                # Stage 2·3 — Planning + Critique (파싱 실패도 plan 재시도로 흡수)
                try:
                    phases = self.orchestrator.plan(goal, profile_text, error_evidence)
                    phases = self.orchestrator.critique(goal, profile_text, phases)
                except ValueError as e:
                    plan_retries += 1
                    case.record("plan_backtrack", {"retry": plan_retries, "evidence": str(e)[:300]})
                    if plan_retries >= self.settings.max_plan_retries:
                        return self._result(False, f"계획 생성 실패: {e}", case, plan_retries=plan_retries)
                    continue
                case.save_json(f"plan-{plan_retries}.json", [p.model_dump() for p in phases])
                case.record("plan", {"retry": plan_retries, "phases": [p.name for p in phases]})

                # Stage 4·5 — Expansion + Execution
                ok, df_final, evidence, done = self._execute_phases(df, phases, profile_text, case)
                if ok:
                    break

                plan_retries += 1
                error_evidence = (error_evidence + "\n" + evidence).strip()
                case.record("plan_backtrack", {"retry": plan_retries, "evidence": evidence})
                if plan_retries >= self.settings.max_plan_retries:
                    return self._result(
                        False, f"plan-level 재시도 한도({self.settings.max_plan_retries}) 초과: {evidence}",
                        case, phases_completed=done, plan_retries=plan_retries,
                    )
        except PipelineAbort as e:
            return self._result(False, f"Monitor abort: {e}", case, plan_retries=plan_retries)

        # Stage 6 — Finalization
        out = Path(output_path) if output_path else case.dir / "output.csv"
        df_final.to_csv(out, index=False)
        case.record("finalize", {"output": str(out), "n_rows": len(df_final)})
        return self._result(
            True, "완료", case,
            output_path=str(out), phases_completed=len(phases), plan_retries=plan_retries,
        )

    # ── phase 루프 (phase-level 백트래킹 포함) ────────────────────────────────
    def _execute_phases(
        self, df: pd.DataFrame, phases: list[Phase], profile_text: str, case: CaseFolder
    ) -> tuple[bool, pd.DataFrame | None, str, int]:
        current = df
        for i, phase in enumerate(phases):
            snapshot = current  # 직전 성공 상태 — 실패 시 여기서 재시작
            # 앞 phase들이 스키마를 바꿨으므로 매 phase 현재 상태를 다시 프로파일
            phase_profile = profile_to_prompt(profile_dataframe(snapshot))
            failures = 0
            hints = ""
            while True:
                ok, new_df, err = self._run_phase(phase, snapshot, phase_profile, case, hints)
                if ok:
                    current = new_df
                    case.record("phase_done", {"phase": phase.name, "rows": len(current)})
                    break
                failures += 1
                case.record("phase_backtrack", {"phase": phase.name, "count": failures, "error": err[:500]})
                if failures > self.settings.max_phase_retries:
                    return False, None, f"phase '{phase.name}' 반복 실패: {err[:500]}", i
                hints = f"Previous attempt failed with: {err[:500]}. Use a different approach."
        return True, current, "", len(phases)

    # ── phase 1개 실행: expand → codegen → progressive sampling → monitor ──
    def _run_phase(
        self, phase: Phase, df: pd.DataFrame, profile_text: str, case: CaseFolder, hints: str
    ) -> tuple[bool, pd.DataFrame | None, str]:
        try:
            specs = self.architect.expand(phase, profile_text, hints)
        except ValueError as e:
            # LLM 응답 파싱 실패도 phase 실패로 취급 → 백트래킹이 복구 시도
            return False, None, f"phase 확장 실패: {e}"
        case.record("expand", {"phase": phase.name, "agents": [s.name for s in specs]})

        current = df
        for spec in specs:
            # 이 시점의 실제 컬럼 기준으로 환각된 입력측 계약 제거
            sanitize_contract(spec.contract, current.columns)
            spec.code = self.architect.generate_code(spec, profile_text)
            ladder = run_ladder(
                spec,
                current,
                refine=self.architect.refine_code,
                max_refine_per_level=self.settings.max_refine_attempts,
                verify=lambda d_in, d_out, _c=spec.contract: verify_contract(_c, d_in, d_out),
            )
            case.save_agent_code(spec.name, spec.code)
            if not ladder.ok:
                return False, None, ladder.last_error

            metrics = StepMetrics(
                rows_in=len(current),
                rows_out=len(ladder.df),
                null_rate_in=overall_null_rate(current),
                null_rate_out=overall_null_rate(ladder.df),
                elapsed_s=ladder.elapsed_s,
                revisions=ladder.revisions,
                peak_mem_mb=ladder.peak_mem_mb,
            )
            report = self.monitor.review(metrics)
            case.record(
                "monitor",
                {"agent": spec.name, "verdict": report.verdict.value, "findings": report.findings},
            )
            if report.verdict == Verdict.ABORT:
                raise PipelineAbort("; ".join(report.findings))
            if report.verdict == Verdict.RETRY:
                return False, None, "Monitor retry 판정: " + "; ".join(report.findings)

            self.architect.register_validated(spec)
            current = ladder.df
        return True, current, ""

    def _result(self, ok: bool, message: str, case: CaseFolder, **kw) -> PipelineResult:
        res = PipelineResult(
            ok=ok, message=message, run_dir=str(case.dir),
            llm_calls=self.llm.calls, total_tokens=self.llm.total_tokens, **kw,
        )
        case.save_json("result.json", res.model_dump())
        return res


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in (".json", ".jsonl"):
        return pd.read_json(path, lines=suffix == ".jsonl")
    raise ValueError(f"지원하지 않는 입력 형식: {suffix} (csv/parquet/json/jsonl)")
