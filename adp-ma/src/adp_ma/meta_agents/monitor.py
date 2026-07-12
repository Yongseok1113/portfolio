"""Monitor 메타-에이전트 (논문 §Rule-based Monitoring).

모든 ground agent 실행 직후 LLM 호출 없이 규칙만으로 상태를 판정한다.
임계값 기본치는 논문 표를 따른다. (pause·비용 추적은 로드맵)
"""

from enum import Enum

from pydantic import BaseModel, Field

from adp_ma.contracts import ContractVerificationResult


class Verdict(str, Enum):
    CONTINUE = "continue"
    WARN = "warn"
    RETRY = "retry"
    ABORT = "abort"


_SEVERITY = {v: i for i, v in enumerate([Verdict.CONTINUE, Verdict.WARN, Verdict.RETRY, Verdict.ABORT])}


class StepMetrics(BaseModel):
    rows_in: int
    rows_out: int
    null_rate_in: float = 0.0
    null_rate_out: float = 0.0
    elapsed_s: float = 0.0
    revisions: int = 0
    peak_mem_mb: float = 0.0


class Thresholds(BaseModel):
    revision_warn: int = 2
    revision_critical: int = 4
    row_drop_warn: float = 0.30
    row_drop_critical: float = 0.90
    row_growth_warn: float = 5.00
    null_increase_warn: float = 0.20
    time_warn_s: float = 60.0
    time_critical_s: float = 300.0


class MonitorReport(BaseModel):
    verdict: Verdict
    findings: list[str] = Field(default_factory=list)
    metrics: StepMetrics


class Monitor:
    def __init__(self, thresholds: Thresholds | None = None):
        self.t = thresholds or Thresholds()

    def review(
        self,
        metrics: StepMetrics,
        contract_result: ContractVerificationResult | None = None,
        *,
        expect_row_reduction: bool = False,
    ) -> MonitorReport:
        findings: list[str] = []
        verdict = Verdict.CONTINUE

        def escalate(v: Verdict, message: str):
            nonlocal verdict
            findings.append(f"[{v.value}] {message}")
            if _SEVERITY[v] > _SEVERITY[verdict]:
                verdict = v

        t = self.t
        if metrics.rows_in > 0:
            drop = 1.0 - metrics.rows_out / metrics.rows_in
            growth = metrics.rows_out / metrics.rows_in - 1.0
            if drop >= t.row_drop_critical and not expect_row_reduction:
                # 무언의 데이터 소실 의심 → 해당 단계 재시도
                escalate(Verdict.RETRY, f"행 {drop:.0%} 소실 ({metrics.rows_in}→{metrics.rows_out})")
            elif drop >= t.row_drop_critical and expect_row_reduction:
                # 집계·중복제거처럼 행 감소가 계약된 단계 — 기록만 남긴다
                escalate(Verdict.WARN, f"행 {drop:.0%} 감소 (계약된 축소, {metrics.rows_in}→{metrics.rows_out})")
            elif drop >= t.row_drop_warn:
                escalate(Verdict.WARN, f"행 {drop:.0%} 감소 ({metrics.rows_in}→{metrics.rows_out})")
            if growth >= t.row_growth_warn:
                escalate(Verdict.WARN, f"행 {growth:.0%} 증가 ({metrics.rows_in}→{metrics.rows_out})")

        null_increase = metrics.null_rate_out - metrics.null_rate_in
        if null_increase >= t.null_increase_warn:
            escalate(Verdict.WARN, f"null 비율 {null_increase:.0%}p 증가")

        if metrics.elapsed_s >= t.time_critical_s:
            escalate(Verdict.ABORT, f"실행 시간 {metrics.elapsed_s:.0f}s 초과 (한도 {t.time_critical_s:.0f}s)")
        elif metrics.elapsed_s >= t.time_warn_s:
            escalate(Verdict.WARN, f"실행 시간 {metrics.elapsed_s:.0f}s")

        if metrics.revisions >= t.revision_critical:
            escalate(Verdict.ABORT, f"코드 수정 {metrics.revisions}회 — 접근 자체가 잘못됐을 가능성")
        elif metrics.revisions >= t.revision_warn:
            escalate(Verdict.WARN, f"코드 수정 {metrics.revisions}회")

        if contract_result is not None:
            for viol in contract_result.violations:
                if viol.severity == "critical":
                    escalate(Verdict.RETRY, f"계약 위반: {viol.message}")
                else:
                    escalate(Verdict.WARN, f"계약 경고: {viol.message}")

        return MonitorReport(verdict=verdict, findings=findings, metrics=metrics)
