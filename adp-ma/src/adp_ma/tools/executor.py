"""tool plan 실행기 — LLM이 고른 도구 체인을 순차 적용한다.

도구는 검증된 코드지만 파라미터는 LLM 산출물이므로,
실행 전 이름·파라미터를 검증하고 실패는 문자열 오류로 돌려
codegen 폴백/refine 루프가 이어받게 한다.
"""

import inspect
import time
from dataclasses import dataclass

import pandas as pd

from adp_ma.tools.base import TOOLS


@dataclass
class ToolPlanResult:
    ok: bool
    df: pd.DataFrame | None
    error: str = ""
    steps_done: int = 0
    elapsed_s: float = 0.0


def validate_tool_plan(tool_plan: list[dict]) -> str:
    """실행 전 정적 검증. 문제 없으면 빈 문자열."""
    if not tool_plan:
        return "빈 tool plan"
    for i, step in enumerate(tool_plan):
        if not isinstance(step, dict) or "tool" not in step:
            return f"step {i}: {{'tool': ..., 'params': ...}} 형식이 아님"
        name = step["tool"]
        if name not in TOOLS:
            return f"step {i}: 알 수 없는 도구 '{name}'"
        params = step.get("params") or {}
        if not isinstance(params, dict):
            return f"step {i} ({name}): params는 객체여야 함"
        sig = inspect.signature(TOOLS[name].fn)
        valid = set(sig.parameters) - {"df"}
        unknown = set(params) - valid
        if unknown:
            return f"step {i} ({name}): 알 수 없는 파라미터 {sorted(unknown)}"
        # 기본값 없는 필수 파라미터 누락 검사
        required = {
            p.name
            for p in sig.parameters.values()
            if p.name != "df" and p.default is inspect.Parameter.empty
        }
        missing = required - set(params)
        if missing:
            return f"step {i} ({name}): 필수 파라미터 누락 {sorted(missing)}"
    return ""


def execute_tool_plan(tool_plan: list[dict], df: pd.DataFrame) -> ToolPlanResult:
    error = validate_tool_plan(tool_plan)
    if error:
        return ToolPlanResult(ok=False, df=None, error=error)

    current = df
    t0 = time.perf_counter()
    for i, step in enumerate(tool_plan):
        name = step["tool"]
        params = step.get("params") or {}
        try:
            current = TOOLS[name].run(current, **params)
        except Exception as e:
            return ToolPlanResult(
                ok=False,
                df=None,
                error=f"step {i} ({name}) 실행 실패: {type(e).__name__}: {e}",
                steps_done=i,
                elapsed_s=time.perf_counter() - t0,
            )
        if not isinstance(current, pd.DataFrame):
            return ToolPlanResult(
                ok=False, df=None,
                error=f"step {i} ({name}): DataFrame이 아닌 결과", steps_done=i,
            )
    return ToolPlanResult(
        ok=True, df=current, steps_done=len(tool_plan), elapsed_s=time.perf_counter() - t0
    )
