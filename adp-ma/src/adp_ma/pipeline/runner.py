"""파이프라인 러너 — 논문의 6단계 사이클과 2단계 백트래킹을 조립한다.

1 Data Understanding → 2 High-Level Planning → 3 Critique →
4 Phase Expansion → 5 Ground-Agent Execution (progressive sampling) →
6 Finalization

phase-level 백트래킹: 직전 성공 스냅샷으로 되돌리고 Architect가 재확장.
plan-level 백트래킹: phase 실패 누적 시 오류 증거와 함께 전체 재계획.
"""

from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

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

# train/test 결합 파이프라인의 분할 마커 (M3 — 모델링용 보호 컬럼)
_SPLIT_COL = "__adp_split"


class PipelineResult(BaseModel):
    ok: bool
    message: str = ""
    run_dir: str = ""
    output_path: str | None = None
    phases_completed: int = 0
    plan_retries: int = 0
    llm_calls: int = 0
    total_tokens: int = 0
    # 역할별 모델 라우팅 사용량 — 라우팅 효과·비용 추적
    tokens_by_model: dict[str, int] = Field(default_factory=dict)
    calls_by_model: dict[str, int] = Field(default_factory=dict)
    # M3 — 모델링 결과 (test 데이터 제공 시)
    submission_path: str | None = None
    best_model: str = ""
    cv_score: float | None = None
    # ③ — case folder MinIO 아카이빙 위치 (archive_to_minio 시)
    archived_to: str | None = None


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
        # HITL 체크포인트 (M4) — 계획 확정 후 실행 전에 호출, False면 반려
        # (AutoKaggle의 UserInteractionEnabled 대응. CLI --interactive가 설정)
        self.plan_reviewer: object | None = None
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
        test_data: str | Path | None = None,
        sample_submission: str | Path | None = None,
        target: str | None = None,
    ) -> PipelineResult:
        """예외 안전 진입점 — 예상 밖 오류(LLM 429, 네트워크 등)에도
        감사 추적과 MinIO 아카이브가 반드시 남는다."""
        case = CaseFolder(self.settings.runs_dir)
        try:
            return self._run_inner(
                case, input_path, goal, output_path, task_doc,
                test_data, sample_submission, target,
            )
        except Exception as e:  # noqa: BLE001 — 최후 방어선: 결과·아카이브 보존
            import traceback

            case.record(
                "fatal",
                {"error": f"{type(e).__name__}: {str(e)[:800]}",
                 "trace": traceback.format_exc(limit=6)[-1500:]},
            )
            return self._result(
                False, f"실행 오류: {type(e).__name__}: {str(e)[:300]}", case
            )

    def _run_inner(
        self,
        case: CaseFolder,
        input_path: str | Path,
        goal: str,
        output_path: str | Path | None = None,
        task_doc: str | Path | None = None,
        test_data: str | Path | None = None,
        sample_submission: str | Path | None = None,
        target: str | None = None,
    ) -> PipelineResult:
        self._task_doc = Path(task_doc).read_text(encoding="utf-8") if task_doc else ""
        self._brief = ""
        self._report_sections = []
        self._split_active = False
        self._submission_path = None
        self._model_report = None
        self._guard = ""
        self._execute = self._make_executor(case.run_id)
        case.record("executor", {"mode": self.settings.executor})
        case.record(
            "llm_routing",
            {
                "model": self.llm.model_for(),
                "model_light": self.llm.model_for(light=True),
                "endpoint": self.llm.endpoint_for(),
                "endpoint_light": self.llm.endpoint_for(light=True),
            },
        )
        df = _read_table(self._resolve_input(input_path, case))

        # ── M3: test 데이터 제공 시 train+test 결합 파이프라인 + 모델링 활성 ──
        if test_data is not None:
            if sample_submission is None:
                return self._result(False, "--test-data 사용 시 --sample-submission 필수", case)
            test_df = _read_table(Path(test_data))
            self._sample_submission = pd.read_csv(sample_submission)
            self._id_col = str(self._sample_submission.columns[0])
            self._target = str(target or self._sample_submission.columns[1])
            if self._target not in df.columns:
                return self._result(False, f"target 컬럼 '{self._target}' 이 train에 없음", case)
            if self._id_col not in df.columns or self._id_col not in test_df.columns:
                return self._result(False, f"id 컬럼 '{self._id_col}' 이 train/test에 없음", case)
            # 구조적 target 보호 — LLM 단계가 target을 스케일/변형해도
            # 모델링 직전에 id 기준으로 원본을 복원한다 (프롬프트 가드는 보조 수단)
            self._target_map = dict(zip(df[self._id_col], df[self._target]))
            df = pd.concat(
                [df.assign(**{_SPLIT_COL: "train"}), test_df.assign(**{_SPLIT_COL: "test"})],
                ignore_index=True,
            )
            self._split_active = True
            self._guard = (
                "\n\n## Protected columns (MUST keep intact)\n"
                f"- '{_SPLIT_COL}': train/test split marker — never drop, modify, encode or use as a feature\n"
                f"- '{self._id_col}': row identifier — never drop or transform\n"
                f"- '{self._target}': prediction target — never impute, scale, encode or transform it; "
                "NaN on test rows is expected; never drop rows because this column is null"
            )
            case.record(
                "split",
                {"train": len(df) - len(test_df), "test": len(test_df),
                 "target": self._target, "id": self._id_col},
            )

        # Stage 1 — Data Understanding
        profile = profile_dataframe(df)
        case.save_json("profile.json", profile)
        case.record("data_understanding", {"n_rows": profile["n_rows"], "n_cols": profile["n_cols"]})
        profile_text = profile_to_prompt(profile)

        # ── kaggle 워크플로: 고정 6-phase 스켈레톤 (plan-level 재계획 없음) ──
        if self.settings.workflow == "kaggle":
            phases = kaggle_phases(goal, with_modeling=self._split_active)
            case.save_json("plan-kaggle.json", [p.model_dump() for p in phases])
            case.record("plan", {"workflow": "kaggle", "phases": [p.name for p in phases]})
            if self.plan_reviewer is not None and not self.plan_reviewer(phases):
                case.record("hitl", {"decision": "rejected"})
                return self._result(False, "사용자가 계획을 반려함 (HITL)", case)
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
            model_kw = {}
            if self._model_report is not None:
                model_kw = {
                    "submission_path": self._submission_path,
                    "best_model": self._model_report.best_model,
                    "cv_score": self._model_report.best_score,
                }
            message = (
                "완료 (kaggle 워크플로: 모델링·submission 포함)"
                if self._split_active
                else "완료 (kaggle 워크플로: 클리닝·FE까지 — test 데이터 없어 모델링 생략)"
            )
            return self._result(
                True, message, case,
                output_path=str(out), phases_completed=done, **model_kw,
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
                if self.plan_reviewer is not None and not self.plan_reviewer(phases):
                    case.record("hitl", {"decision": "rejected"})
                    return self._result(False, "사용자가 계획을 반려함 (HITL)", case, plan_retries=plan_retries)

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
            if phase.kind == "modeling":
                # M3 — 검증된 자체 코드로 모델 선택·학습·예측·submission (LLM 무관)
                ok, err = self._run_modeling(current, case)
                if not ok:
                    return False, None, err, i
                continue

            snapshot = current  # 직전 성공 상태 — 실패 시 여기서 재시작
            # 앞 phase들이 스키마를 바꿨으므로 매 phase 현재 상태를 다시 프로파일
            phase_profile = profile_to_prompt(profile_dataframe(snapshot))
            if self._guard:
                phase_profile += self._guard
            if self._brief:
                phase_profile += "\n\n## Task brief\n" + self._brief
            if insights:
                phase_profile += "\n\n## EDA findings so far\n" + "\n".join(insights)
            self._phase_events = []  # M2 — Summarizer용 이벤트 수집 초기화
            failures = 0
            hints = ""
            while True:
                ok, new_df, err = self._run_phase(phase, snapshot, phase_profile, case, hints)
                if ok and self._split_active:
                    # 보호 컬럼(분할 마커·id) 유실은 phase 실패로 처리 → 백트래킹
                    missing = [c for c in (_SPLIT_COL, self._id_col) if c not in new_df.columns]
                    if missing:
                        ok, err = False, f"보호 컬럼 유실: {missing} — 제거·변형 금지 대상"
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
        gate = self._make_gate(spec, profile_text, case) if self.settings.unit_tests_enabled else None
        ladder = run_ladder(
            spec,
            current,
            refine=self.architect.refine_code,
            max_refine_per_level=self.settings.max_refine_attempts,
            verify=lambda d_in, d_out, _c=spec.contract: verify_contract(_c, d_in, d_out),
            execute=self._execute,
            gate=gate,
        )
        case.save_agent_code(spec.name, spec.code)
        if not ladder.ok:
            return None, 0.0, ladder.revisions, 0.0, ladder.last_error
        return ladder.df, ladder.elapsed_s, ladder.revisions, ladder.peak_mem_mb, ""

    # ── 단위 테스트 게이트 (M4) ──────────────────────────────────────────────
    def _make_gate(self, spec, profile_text: str, case: CaseFolder):
        """spec용 단위 테스트를 생성하고 게이트 함수로 감싼다.

        생성된 테스트 자체가 틀렸을 수 있으므로, 게이트 실패가 2회 누적되면
        게이트를 무력화한다 (계약·샘플링 검증은 계속 유효) — 논문의
        assistance mechanism(반복 오류 시 루프 탈출)의 축소판.
        """
        from adp_ma.ground.sandbox import run_gate_code

        test_code = self.architect.generate_unit_test(spec, profile_text)
        case.save_agent_code(f"{spec.name}__test", test_code)
        failures = {"count": 0, "disabled": False}

        def gate(d_in, d_out) -> str:
            if failures["disabled"]:
                return ""
            err = run_gate_code(test_code, d_in, d_out)
            if err:
                failures["count"] += 1
                if failures["count"] >= 2:
                    failures["disabled"] = True
                    case.record(
                        "gate_disabled",
                        {"agent": spec.name, "reason": err[:200],
                         "note": "게이트 실패 반복 — 테스트 자체 결함 가능성으로 무력화"},
                    )
                    return ""
            return err

        return gate

    # ── modeling phase (M3): 모델 선택·학습·예측 → submission.csv ───────────
    def _run_modeling(self, df: pd.DataFrame, case: CaseFolder) -> tuple[bool, str]:
        from adp_ma.modeling import build_submission, train_validate_predict

        if _SPLIT_COL not in df.columns:
            return False, f"분할 마커 '{_SPLIT_COL}' 유실 — 모델링 불가"
        train = df[df[_SPLIT_COL] == "train"].drop(columns=[_SPLIT_COL]).copy()
        test = df[df[_SPLIT_COL] == "test"].drop(columns=[_SPLIT_COL]).copy()
        # target 원본 복원 (id 기준) — 파이프라인이 target을 변형했어도 무해화
        train[self._target] = train[self._id_col].map(self._target_map)
        restored = int(train[self._target].notna().sum())
        case.record("target_restore", {"restored": restored, "train_rows": len(train)})
        if restored == 0:
            return False, f"target 복원 실패 — id 컬럼 '{self._id_col}' 값이 변형된 듯"
        try:
            report, preds = train_validate_predict(
                train, test, self._target, exclude=[self._id_col]
            )
            submission = build_submission(self._sample_submission, test, preds)
        except (KeyError, ValueError) as e:
            return False, f"모델링 실패: {e}"

        sub_path = case.dir / "submission.csv"
        submission.to_csv(sub_path, index=False)
        self._submission_path = str(sub_path)
        self._model_report = report
        case.record(
            "modeling",
            {"task": report.task, "best": report.best_model, "cv_scores": report.cv_scores,
             "metric": report.metric, "n_features": len(report.features)},
        )
        summary = (
            f"- task: {report.task} (target={report.target})\n"
            f"- CV 비교: {report.cv_scores} → best **{report.best_model}** "
            f"({report.metric}={report.best_score})\n"
            f"- 피처 {len(report.features)}개, train {report.n_train}행 → test {report.n_test}행 예측\n"
            f"- submission: {sub_path.name}"
        )
        case.save_text("report-modeling.md", summary)
        self._report_sections.append(("modeling", summary))
        return True, ""

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
        # 경량 티어: 프로파일을 받아 서술 인사이트를 내는 역할 — 코드가 아니라 후속 프롬프트의 컨텍스트
        text = self.llm.chat(_ANALYSIS_SYSTEM, user, light=True).strip()
        insights.append(f"### {phase.name}\n{text[:1500]}")
        case.save_text(f"analysis-{phase.name}.md", text)
        case.record("analysis", {"phase": phase.name, "chars": len(text)})

    def _result(self, ok: bool, message: str, case: CaseFolder, **kw) -> PipelineResult:
        res = PipelineResult(
            ok=ok, message=message, run_dir=str(case.dir),
            llm_calls=self.llm.calls, total_tokens=self.llm.total_tokens,
            tokens_by_model=dict(self.llm.tokens_by_model),
            calls_by_model=dict(self.llm.calls_by_model),
            **kw,
        )
        case.save_json("result.json", res.model_dump())
        # result.json까지 저장한 뒤 아카이빙해야 MinIO 사본이 완전하다
        res.archived_to = self._archive_case(case)
        return res

    # ── minio:// 입력 URI 해석 (③) ───────────────────────────────────────────
    def _resolve_input(self, input_path, case: CaseFolder) -> Path:
        """minio://bucket/key 는 임시 파일로 내려받고, 그 외는 로컬 경로 그대로."""
        s = str(input_path)
        if not s.startswith("minio://"):
            return Path(input_path)
        from adp_ma.state.storage import ArtifactStore, parse_minio_uri

        bucket, key = parse_minio_uri(s)
        store = ArtifactStore.from_settings(self.settings)
        if bucket != store.bucket:
            store.bucket = bucket
        local = case.dir / "input" / Path(key).name
        store.download_file(key, local)
        case.record("input_download", {"uri": s, "local": str(local)})
        return local

    # ── case folder → MinIO 아카이빙 (③) ─────────────────────────────────────
    def _archive_case(self, case: CaseFolder) -> str | None:
        """settings.archive_to_minio 시 case folder를 MinIO runs/<id>/로 업로드.

        아카이빙 실패는 실행 결과에 영향 주지 않는다 (부가 기능) — 경고만 기록.
        """
        if not self.settings.archive_to_minio:
            return None
        try:
            from adp_ma.state.storage import ArtifactStore

            store = ArtifactStore.from_settings(self.settings)
            prefix = f"runs/{case.run_id}"
            n = store.upload_dir(case.dir, prefix)
            case.record("archive", {"prefix": prefix, "files": n})
            return f"minio://{self.settings.minio_bucket}/{prefix}"
        except Exception as e:  # noqa: BLE001 — 아카이빙 실패는 치명적이지 않음
            case.record("archive_failed", {"error": str(e)[:200]})
            return None


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in (".json", ".jsonl"):
        return pd.read_json(path, lines=suffix == ".jsonl")
    raise ValueError(f"지원하지 않는 입력 형식: {suffix} (csv/parquet/json/jsonl)")
