"""ML Tools Library 기반 구조 (AutoKaggle §Machine Learning Tools).

검증된 함수 라이브러리 — Architect(Developer 역할)는 목표를 먼저
도구 조합(tool plan)으로 시도하고, 도구로 부족할 때만 코드를 생성한다.
도구는 사람이 작성·테스트한 코드이므로 progressive sampling 없이
바로 전체 데이터에 실행한다 (계약·Monitor 검증은 동일하게 적용).
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class ToolSpec:
    name: str
    description: str          # LLM 카탈로그에 노출되는 한 줄 설명
    fn: Callable[..., pd.DataFrame]
    params: dict[str, str] = field(default_factory=dict)  # 파라미터명 → 설명

    def run(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        return self.fn(df, **params)


TOOLS: dict[str, ToolSpec] = {}


def register(name: str, description: str, params: dict[str, str] | None = None):
    """도구 등록 데코레이터 — 시그니처 첫 인자는 반드시 df."""

    def deco(fn):
        sig = list(inspect.signature(fn).parameters)
        assert sig and sig[0] == "df", f"{name}: 첫 파라미터는 df여야 함"
        TOOLS[name] = ToolSpec(name=name, description=description, fn=fn, params=params or {})
        return fn

    return deco


def describe_tools() -> str:
    """LLM 프롬프트용 도구 카탈로그."""
    lines = []
    for t in TOOLS.values():
        params = ", ".join(f"{k}: {v}" for k, v in t.params.items()) or "(파라미터 없음)"
        lines.append(f"- {t.name}: {t.description}\n    params: {params}")
    return "\n".join(lines)


def numeric_columns(df: pd.DataFrame, columns: list[str] | None = None) -> list[str]:
    """columns 미지정 시 수치형 전체를 대상으로 하는 도구들의 공통 헬퍼."""
    if columns:
        return [c for c in columns if c in df.columns]
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
