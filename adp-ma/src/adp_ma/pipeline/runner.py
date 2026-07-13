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

import re

from adp_ma.config import Settings
from adp_ma.contracts import RowCountRelation, sanitize_contract, verify_contract
from adp_ma.ground import run_agent_code, run_ladder
from adp_ma.llm import LLMClient
from adp_ma.meta_agents import (
    Architect,
    Monitor,
    Orchestrator,
    Phase,
    Reader,
    StepMetrics,
    Summarizer,
    Verdict,
    assemble_final_report,
)
from adp_ma.pipeline.workflows import kaggle_phases
from adp_ma.profiling import overall_null_rate, profile_dataframe, profile_to_prompt
from adp_ma.state import CaseFolder
from adp_ma.tools import execute_tool_plan

_ANALYSIS_SYSTEM = """\
You are the data analyst of an autonomous pipeline. Given the phase objective,
the user's goal and the current data profile, produce 3-6 concise bullet findings
that directly inform the NEXT phases. Reference exact column names.
Plain text bullets only — no code, no JSON."""


class PipelineAbort(Exception):
    """Monitor가 abort 판정을 내렸을 때 실행 전체를 중단한다."""


# 행 감소가 목적 자체인 작업의 신호 — Monitor의 행 소실 룰 완화에 사용
_AGGREGATION_RE = re.compile(
    r"aggregat|group\s*by|groupby|summar|pivot|dedup|duplicate|집계|합계|요약|중복",
    re.IGNORECASE,
)


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
        self.reader = Reader(self.llm)          # M2 — task brief
        self.summarizer = Summarizer(self.llm)  # M2 — phase report 압축
        self._execute = run_agent_code  # run 시작 시 executor 설정에 따라 교체
        # kaggle 워크플로 실행 중 최종 report.md 조립용 상태
        self._brief = ""
        self._report_sections: list[tuple[str, str]] = []
        self._phase_events: list[dict] = []

    def _make_executor(self, run_id: str):
        if self.settings.executor == "k8s":
            from adp_ma.ground.k8s_executor import K8sJobExecutor
            from adp_ma.state.storage import ArtifactStore

            store = ArtifactStore.from_settings(self.settings)
            return K8sJobExecutor(self.settings, store, run_id)
        return run_agent_code

    def run(
        self,
        input_path: str | Path,
        goal: str,
        output_path: str | Path | None = None,
        task_doc: str | Path | None = None,
    ) -> PipelineResult:
        case = CaseFolder(self.settings.runs_dir)
        self._task_doc = Path(task_doc).read_text(encoding="utf-8") if task_doc else ""
        self._brief = ""
        self._report_sections = []
        self._execute = self._make_executor(case.run_id)
        case.record("executor", {"mode": self.settings.executor})
        df = _read_table(Path(input_path))

        # Stage 1 — Data Understanding
        profile = profile_dataframe(df)
        case.save_json("profile.json", profile)
        case.record("data_understanding", {"n_rows": profile["n_rows"], "n_cols": profile["n_cols"]})
        profile_text = profile_to_prompt(profile)

        # ── kaggle 워크플로: 고정 6-phase 스켈레톤 (plan-level 재계획 없음) ──
        if self.settings.workflow == "kaggle":
            phases = kaggle_phases(goal)
            case.save_json("plan-kaggle.json", [p.model_dump() for p in phases])
            case.record("plan", {"workflow": "kaggle", "phases": [p.name for p in phases]})
            try:
                ok, df_final, evidence, done = self._execute_phases(
                    df, phases, profile_text, case, goal=goal
                )
            except PipelineAbort as e:
                return self._result(False, f"Monitor abort: {e}", case)
            if not ok:
                return self._result(False, f"kaggle 워크플로 실패: {evidence}", case, phases_completed=done)
            out = Path(output_path) if output_path else case.dir / "output.csv"
            df_final.to_csv(out, index=False)
            case.record("finalize", {"output": str(out), "n_rows": len(df_final)})
            # M2 — 최종 리포트 조립 (사람이 읽는 실행 요약)
            report_md = assemble_final_report(
                goal, self._brief, self._report_sections,
                {
                    "output": str(out),
                    "rows": len(df_final),
                    "columns": ", ".join(map(str, df_final.columns)),
                    "llm_calls": self.llm.calls,
                    "tokens": self.llm.total_tokens,
                },
            )
            case.save_text("report.md", report_md)
            return self._result(
                True, "완료 (kaggle 워크플로: 클리닝·FE까지 — 모델링은 M3)", case,
                output_path=str(out), phases_completed=done,
            )

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
        self,
        df: pd.DataFrame,
        phases: list[Phase],
        profile_text: str,
        case: CaseFolder,
        goal: str = "",
    ) -> tuple[bool, pd.DataFrame | None, str, int]:
        current = df
        insights: list[str] = []  # analysis phase가 쌓는 EDA 인사이트 — 이후 phase 컨텍스트
        for i, phase in enumerate(phases):
            if phase.kind == "skip":
                case.record("phase_skipped", {"phase": phase.name, "reason": phase.objective})
                continue
            if phase.kind == "reader":
                # M2 — Reader: task brief 생성, 전 phase 공통 컨텍스트
                profile_now = profile_to_prompt(profile_dataframe(current))
                self._brief = self.reader.brief(goal, profile_now, self._task_doc)
                case.save_text("brief.md", self._brief)
                case.record("reader", {"phase": phase.name, "task_doc": bool(self._task_doc)})
                continue
            if phase.kind == "analysis":
                self._run_analysis(phase, current, goal, insights, case)
                if insights:  # 최종 리포트에 분석 원문 수록
                    self._report_sections.append((phase.name, insights[-1]))
                continue

            snapshot = current  # 직전 성공 상태 — 실패 시 여기서 재시작
            # 앞 phase들이 스키마를 바꿨으므로 매 phase 현재 상태를 다시 프로파일
            phase_profile = profile_to_prompt(profile_dataframe(snapshot))
            if self._brief:
                phase_profile += "\n\n## Task brief\n" + self._brief
            if insights:
                phase_profile += "\n\n## EDA findings so far\n" + "\n".join(insights)
            self._phase_events = []  # M2 — Summarizer용 이벤트 수집 초기화
            failures = 0
            hints = ""
            while True:
                ok, new_df, err = self._run_phase(phase, snapshot, phase_profile, case, hints)
                if ok:
                    current = new_df
                    case.record("phase_done", {"phase": phase.name, "rows": len(current)})
                    # M2 — Summarizer: phase 실행 로그를 압축해 이후 phase 컨텍스트로
                    if self.settings.workflow == "kaggle" and self._phase_events:
                        summary = self.summarizer.summarize_phase(phase.name, self._phase_events)
                        case.save_text(f"report-{phase.name}.md", summary)
                        case.record("phase_report", {"phase": phase.name, "chars": len(summary)})
                        insights.append(f"### {phase.name} (완료된 작업)\n{summary[:800]}")
                        self._report_sections.append((phase.name, summary))
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

            out_df, elapsed_s, revisions, peak_mem_mb, err = self._execute_spec(
                spec, current, profile_text, case
            )
            if err:
                return False, None, err

            metrics = StepMetrics(
                rows_in=len(current),
                rows_out=len(out_df),
                null_rate_in=overall_null_rate(current),
                null_rate_out=overall_null_rate(out_df),
                elapsed_s=elapsed_s,
                revisions=revisions,
                peak_mem_mb=peak_mem_mb,
            )
            # 집계·중복제거처럼 행 감소가 의도된 단계는 행 소실 룰을 완화
            expect_reduction = (
                spec.contract.row_count == RowCountRelation.LESS_OR_EQUAL
                or bool(_AGGREGATION_RE.search(spec.objective))
            )
            report = self.monitor.review(metrics, expect_row_reduction=expect_reduction)
            case.record(
                "monitor",
                {"agent": spec.name, "verdict": report.verdict.value, "findings": report.findings},
            )
            # M2 — Summarizer용 이벤트 (phase 단위로 수집)
            self._phase_events.append(
                {
                    "agent": spec.name,
                    "objective": spec.objective[:200],
                    "mode": "tools" if spec.tool_plan else "codegen",
                    "tools": [s["tool"] for s in spec.tool_plan],
                    "rows": f"{metrics.rows_in}->{metrics.rows_out}",
                    "verdict": report.verdict.value,
                    "findings": report.findings,
                }
            )
            if report.verdict == Verdict.ABORT:
                raise PipelineAbort("; ".join(report.findings))
            if report.verdict == Verdict.RETRY:
                return False, None, "Monitor retry 판정: " + "; ".join(report.findings)

            if spec.code:  # codegen 경로만 라이브러리에 등록 (도구는 이미 검증된 코드)
                self.architect.register_validated(spec)
            current = out_df
        return True, current, ""

    # ── spec 1개 실행: 도구 우선 → codegen 폴백 ─────────────────────────────
    def _execute_spec(
        self, spec, current: pd.DataFrame, profile_text: str, case: CaseFolder
    ) -> tuple[pd.DataFrame | None, float, int, float, str]:
        """반환: (출력 df, elapsed_s, revisions, peak_mem_mb, 오류)."""
        if self.settings.tools_enabled:
            spec.tool_plan = self.architect.plan_tools(spec, profile_text)
            if spec.tool_plan:
                tres = execute_tool_plan(spec.tool_plan, current)
                contract_err = ""
                if tres.ok:
                    cvr = verify_contract(spec.contract, current, tres.df)
                    if not cvr.ok:
                        contract_err = "SchemaContract 위반: " + "; ".join(
                            v.message for v in cvr.critical
                        )
                if tres.ok and not contract_err:
                    case.record(
                        "tool_plan",
                        {"agent": spec.name, "tools": [s["tool"] for s in spec.tool_plan]},
                    )
                    return tres.df, tres.elapsed_s, 0, 0.0, ""
                # 도구 실패는 치명적이지 않다 — codegen으로 폴백
                case.record(
                    "tool_plan_fallback",
                    {"agent": spec.name, "error": (tres.error or contract_err)[:300]},
                )
                spec.tool_plan = []

        spec.code = self.architect.generate_code(spec, profile_text)
        ladder = run_ladder(
            spec,
            current,
            refine=self.architect.refine_code,
            max_refine_per_level=self.settings.max_refine_attempts,
            verify=lambda d_in, d_out, _c=spec.contract: verify_contract(_c, d_in, d_out),
            execute=self._execute,
        )
        case.save_agent_code(spec.name, spec.code)
        if not ladder.ok:
            return None, 0.0, ladder.revisions, 0.0, ladder.last_error
        return ladder.df, ladder.elapsed_s, ladder.revisions, ladder.peak_mem_mb, ""

    # ── analysis phase: 데이터 변경 없이 인사이트만 생산 ──────────────────────
    def _run_analysis(
        self, phase: Phase, df: pd.DataFrame, goal: str, insights: list[str], case: CaseFolder
    ):
        profile_text = profile_to_prompt(profile_dataframe(df))
        user = (
            f"## Phase\n{phase.name}: {phase.objective}\n\n"
            f"## Goal\n{goal}\n\n## Data profile\n{profile_text}"
        )
        if insights:
            user += "\n\n## Prior findings\n" + "\n".join(insights)
        text = self.llm.chat(_ANALYSIS_SYSTEM, user).strip()
        insights.append(f"### {phase.name}\n{text[:1500]}")
        case.save_text(f"analysis-{phase.name}.md", text)
        case.record("analysis", {"phase": phase.name, "chars": len(text)})

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
