"""Progressive Sampling (논문 §Progressive Sampling).

XS(10) → S(100) → M(1000) → FULL 순으로 샘플 크기를 키우며 코드를 검증하고,
레벨별 실패 시 refine 콜백(코딩 LLM)으로 코드를 수정해 재시도한다.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from adp_ma.contracts import ContractVerificationResult
from adp_ma.ground.base import GroundAgentSpec
from adp_ma.ground.sandbox import SandboxResult, run_agent_code

SAMPLE_LADDER: list[tuple[str, int | None]] = [
    ("XS", 10),
    ("S", 100),
    ("M", 1000),
    ("FULL", None),
]

RefineFn = Callable[[GroundAgentSpec, str], str]
VerifyFn = Callable[[pd.DataFrame, pd.DataFrame], ContractVerificationResult | None]
# run_agent_code와 동일 시그니처 — K8sJobExecutor 등으로 대체 가능
ExecuteFn = Callable[[str, pd.DataFrame], "SandboxResult"]
# 단위 테스트 게이트 (M4): (df_in, df_out) → 오류 메시지 또는 ""
GateFn = Callable[[pd.DataFrame, pd.DataFrame], str]
# 게이트는 대표성 있는 샘플에서만 실행 (작은 샘플의 위양성 회피)
_GATE_LEVELS = ("M", "FULL")


@dataclass
class LadderResult:
    ok: bool
    df: pd.DataFrame | None
    revisions: int
    last_error: str
    level_reached: str
    elapsed_s: float = 0.0
    peak_mem_mb: float = 0.0


def run_ladder(
    spec: GroundAgentSpec,
    df: pd.DataFrame,
    refine: RefineFn,
    *,
    max_refine_per_level: int = 3,
    verify: VerifyFn | None = None,
    execute: ExecuteFn = run_agent_code,
    gate: GateFn | None = None,
) -> LadderResult:
    revisions = 0
    for level, n in SAMPLE_LADDER:
        if n is None or len(df) <= n:
            sample = df
        else:
            # random_state 고정으로 재현성 확보 (head보다 대표성 높음)
            sample = df.sample(n, random_state=0)

        attempts = 0
        while True:
            res = execute(spec.code, sample)
            contract_error = ""
            if res.ok and verify is not None:
                cvr = verify(sample, res.df)
                if cvr is not None and not cvr.ok:
                    contract_error = "SchemaContract 위반: " + "; ".join(
                        v.message for v in cvr.critical
                    )
            gate_error = ""
            if res.ok and not contract_error and gate is not None and level in _GATE_LEVELS:
                # M4 — 단위 테스트 게이트: 스키마 계약이 못 잡는 논리 오류 차단
                gate_error = gate(sample, res.df)
                if gate_error:
                    gate_error = f"단위 테스트 실패: {gate_error}"
            if res.ok and not contract_error and not gate_error:
                break  # 이 레벨 통과 → 승급

            attempts += 1
            revisions += 1
            error = contract_error or gate_error or res.error
            if attempts >= max_refine_per_level:
                return LadderResult(
                    ok=False,
                    df=None,
                    revisions=revisions,
                    last_error=error,
                    level_reached=level,
                )
            spec.code = refine(spec, error)

        if n is None:  # FULL 통과 → 완료
            return LadderResult(
                ok=True,
                df=res.df,
                revisions=revisions,
                last_error="",
                level_reached="FULL",
                elapsed_s=res.elapsed_s,
                peak_mem_mb=res.peak_mem_mb,
            )

    raise AssertionError("unreachable — SAMPLE_LADDER는 FULL로 끝난다")
