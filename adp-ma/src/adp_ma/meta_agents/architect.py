"""Architect 메타-에이전트 (논문 Stage 4 + 코드 생성).

phase를 ground agent 명세(유형·목표·스키마 계약)로 구체화하고,
각 명세에 대해 pandas 코드를 생성·수정한다. 검증된 정의는
라이브러리에 보관해 재사용한다.
"""

import hashlib
import re

from adp_ma.contracts import SchemaContract
from adp_ma.ground import AGENT_TYPES, GroundAgentSpec
from adp_ma.llm import LLMClient, extract_code
from adp_ma.meta_agents.orchestrator import Phase

_EXPAND_SYSTEM = f"""\
You are the Architect of an autonomous data processing system.
Convert one pipeline phase into 1-4 concrete ground agents executed in order.
Agent types: {", ".join(AGENT_TYPES)}.
Each agent transforms a single pandas DataFrame (df in, df out).
Also declare a schema contract per agent. Return JSON:
{{"agents": [{{
  "name": str (short snake_case),
  "agent_type": str,
  "objective": str (precise instruction for a code generator),
  "contract": {{
     "required_input_columns": {{"col": "int|float|str|bool|datetime|any"}},
     "add_columns": {{"col": "dtype"}},
     "preserve_columns": [str],
     "remove_columns": [str],
     "row_count": "equal|less_or_equal|greater_or_equal|any"
  }}
}}]}}
Every agent object MUST include "name", "agent_type" and "objective".
Example agent: {{"name": "drop_duplicates", "agent_type": "transformer",
"objective": "Remove duplicate rows based on order_id, keep first occurrence",
"contract": {{"required_input_columns": {{"order_id": "str"}},
"preserve_columns": ["order_id"], "row_count": "less_or_equal"}}}}
Keep contracts minimal — only what the objective guarantees."""

_CODEGEN_SYSTEM = """\
You write production pandas code for one step of a data pipeline.
Rules:
- Define exactly one function: def run(df: pd.DataFrame) -> pd.DataFrame
- pandas is available as pd, numpy as np. Allowed imports: math, re, datetime, json, statistics, collections, itertools, functools.
- No file I/O, no network, no exec/eval, no global state.
- Never mutate columns you were not asked to touch.
- Handle nulls and unexpected values defensively.
Data preservation (critical, applies to cleaning/parsing steps):
- PRESERVE ROWS while cleaning. Drop rows ONLY when the objective explicitly requires it (e.g. deduplication).
- When parsing, use coercion (pd.to_numeric(..., errors="coerce"), pd.to_datetime(..., errors="coerce")) and KEEP unparseable rows as NaN — never filter them out.
- When standardizing categories, map variants (case, spelling) to canonical values; leave unmatched values as-is or NaN instead of removing rows.
- EXCEPTION — aggregation: if the objective asks for a grouped summary (groupby/sum/집계),
  return the AGGREGATED table itself (one row per group via .agg/.sum, NOT .transform);
  reducing rows this way is required, not data loss.
Return ONLY the code in one ```python block."""

_REFINE_SYSTEM = _CODEGEN_SYSTEM + """

The previous implementation failed. Fix the root cause of the error.
Do not repeat the same approach if the error indicates it cannot work."""

_UNITTEST_SYSTEM = """\
You write ONE validation function for a data pipeline step (M4 unit-test gate).
Rules:
- Define exactly: def check(df_in, df_out) -> None
- Use assert statements with clear messages to verify the step's LOGIC beyond schema:
  value ranges, expected categories, parsing correctness, row-count relations,
  no unintended data loss or value corruption.
- pandas as pd, numpy as np available. Same import whitelist as pipeline code.
- Do NOT re-implement the transformation; only check observable properties.
- 3-6 asserts. They must pass on a CORRECT implementation — be tolerant to NaN
  where the objective allows preserving unparseable values, and to sampled subsets
  (never assert exact row counts unless the objective fixes them).
Return ONLY the code in one ```python block."""

_TOOLPLAN_SYSTEM = """\
You select tools from a validated ML tools library to accomplish one agent's objective.
Prefer tools over custom code whenever they suffice. Chain multiple tools in order if needed.
Column names in params MUST exist in the data profile — copy them exactly.
Check the profile's sample values: formatted number strings ("$1,234.56") need parse_numeric
(convert_dtypes cannot strip symbols). If no tool truly fits a step, return [] — a wrong
tool chain that silently corrupts values is worse than generated code.
Return JSON: {"tool_plan": [{"tool": "<name>", "params": {...}}]}
If the objective cannot be fully done with these tools, return {"tool_plan": []}
(custom code will be generated instead — do not force a partial fit).

## Tool catalog
"""


class Architect:
    def __init__(self, llm: LLMClient):
        self.llm = llm
        # 검증된 에이전트 정의 라이브러리 — (type, objective) 해시 → spec
        self.library: dict[str, GroundAgentSpec] = {}

    # ── Stage 4: Phase Expansion ────────────────────────────────────────────
    def expand(self, phase: Phase, profile_text: str, hints: str = "") -> list[GroundAgentSpec]:
        user = (
            f"## Phase\nname: {phase.name}\nobjective: {phase.objective}\n"
            f"rationale: {phase.rationale}\n\n## Data profile\n{profile_text}"
        )
        if hints:
            user += f"\n\n## Hints from previous failure\n{hints}"
        try:
            data = self.llm.chat_json(_EXPAND_SYSTEM, user)
            specs = _parse_specs(data)
        except ValueError:
            # 약한 모델이 명세 스키마를 못 지키면 phase 목표를 그대로
            # 단일 transformer로 강등해 진행을 보장한다 (계약 없음)
            specs = [
                GroundAgentSpec(
                    name=_slug(phase.name), agent_type="transformer", objective=phase.objective
                )
            ]
        for spec in specs:
            spec.hints = hints
        return specs

    # ── 도구 우선 경로 (AutoKaggle tools library) ────────────────────────────
    def plan_tools(self, spec: GroundAgentSpec, profile_text: str) -> list[dict]:
        """objective를 도구 체인으로 매핑. 불가하면 [] (codegen 폴백)."""
        from adp_ma.tools import describe_tools, validate_tool_plan

        user = (
            f"## Agent objective\n{spec.objective}\n"
            f"## Data profile\n{profile_text}"
        )
        if spec.hints:
            user += f"\n## Hints from previous failure\n{spec.hints}"
        try:
            data = self.llm.chat_json(_TOOLPLAN_SYSTEM + describe_tools(), user)
        except ValueError:
            return []
        plan = data.get("tool_plan", []) if isinstance(data, dict) else data
        if not isinstance(plan, list) or not plan:
            return []
        # 정적 검증 실패한 계획은 버리고 codegen으로 — 도구 오용보다 폴백이 안전
        return plan if validate_tool_plan(plan) == "" else []

    # ── 코드 생성 / 수정 ─────────────────────────────────────────────────────
    def generate_code(self, spec: GroundAgentSpec, profile_text: str) -> str:
        cached = self.library.get(_library_key(spec))
        if cached is not None:
            return cached.code
        user = (
            f"## Agent\ntype: {spec.agent_type}\nobjective: {spec.objective}\n"
            f"## Schema contract\n{spec.contract.model_dump_json()}\n"
            f"## Data profile\n{profile_text}"
        )
        if spec.hints:
            user += f"\n## Hints\n{spec.hints}"
        return extract_code(self.llm.chat(_CODEGEN_SYSTEM, user))

    def generate_unit_test(self, spec: GroundAgentSpec, profile_text: str) -> str:
        """spec의 논리 검증용 check(df_in, df_out) 코드 생성 (M4 게이트)."""
        user = (
            f"## Step objective\n{spec.objective}\n"
            f"## Schema contract\n{spec.contract.model_dump_json()}\n"
            f"## Data profile (input side)\n{profile_text}"
        )
        return extract_code(self.llm.chat(_UNITTEST_SYSTEM, user))

    def refine_code(self, spec: GroundAgentSpec, error: str) -> str:
        user = (
            f"## Agent objective\n{spec.objective}\n"
            f"## Schema contract\n{spec.contract.model_dump_json()}\n"
            f"## Current code\n```python\n{spec.code}\n```\n"
            f"## Error\n{error}"
        )
        return extract_code(self.llm.chat(_REFINE_SYSTEM, user))

    # ── 라이브러리 ───────────────────────────────────────────────────────────
    def register_validated(self, spec: GroundAgentSpec):
        """FULL 실행까지 통과한 에이전트를 재사용 라이브러리에 등록."""
        self.library[_library_key(spec)] = spec.model_copy(deep=True)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "agent"


def _library_key(spec: GroundAgentSpec) -> str:
    raw = f"{spec.agent_type}:{spec.objective}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _parse_specs(data) -> list[GroundAgentSpec]:
    if isinstance(data, dict):
        if "agents" in data:
            data = data["agents"]
        else:
            data = [data]  # 단일 에이전트를 감싸지 않고 반환한 경우
    specs = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        # 약한 모델이 objective 대신 다른 키를 쓰는 경우 흡수
        objective = item.get("objective") or item.get("description") or item.get("task")
        if not objective:
            continue
        item = {**item, "objective": objective}
        agent_type = str(item.get("agent_type", "transformer"))
        if agent_type not in AGENT_TYPES:
            agent_type = "transformer"
        try:
            contract = SchemaContract.model_validate(item.get("contract") or {})
        except Exception:
            contract = SchemaContract()  # 계약 파싱 실패는 무계약으로 진행
        specs.append(
            GroundAgentSpec(
                name=str(item.get("name") or f"agent_{i}"),
                agent_type=agent_type,
                objective=str(item["objective"]),
                contract=contract,
            )
        )
    if not specs:
        raise ValueError(f"ground agent 파싱 실패: {data!r}")
    return specs
