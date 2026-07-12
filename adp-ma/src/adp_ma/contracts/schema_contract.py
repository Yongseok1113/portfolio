"""스키마 계약 (논문 §Schema Contracts — EnhancedSchemaContract 축소 구현).

ground agent 하나가 지켜야 할 입·출력 조건을 선언하고,
실행 결과 DataFrame에 대해 위반 여부를 판정한다.
"""

from enum import Enum

import pandas as pd
from pydantic import BaseModel, Field


class RowCountRelation(str, Enum):
    """출력 행 수가 입력 대비 가져야 하는 관계."""

    EQUAL = "equal"
    LESS_OR_EQUAL = "less_or_equal"
    GREATER_OR_EQUAL = "greater_or_equal"
    ANY = "any"


class ValueConstraint(BaseModel):
    column: str
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: list[str] | None = None
    regex: str | None = None
    unique: bool = False
    no_nulls: bool = False


class SchemaContract(BaseModel):
    # {컬럼명: dtype} — dtype은 int/float/number/bool/datetime/str/any
    required_input_columns: dict[str, str] = Field(default_factory=dict)
    add_columns: dict[str, str] = Field(default_factory=dict)
    preserve_columns: list[str] = Field(default_factory=list)
    remove_columns: list[str] = Field(default_factory=list)
    value_constraints: list[ValueConstraint] = Field(default_factory=list)
    row_count: RowCountRelation = RowCountRelation.ANY


class Violation(BaseModel):
    severity: str  # "warning" | "critical"
    column: str | None = None
    message: str


class ContractVerificationResult(BaseModel):
    ok: bool
    violations: list[Violation] = Field(default_factory=list)

    @property
    def critical(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "critical"]


# 선언 dtype → 허용되는 pandas dtype.kind 집합
_DTYPE_KINDS: dict[str, str] = {
    "int": "iu",
    "float": "fiu",
    "number": "fiu",
    "numeric": "fiu",
    "bool": "b",
    "datetime": "M",
    "str": "OUS",
    "string": "OUS",
    "object": "OUS",
    "category": "O",
}


def _dtype_ok(declared: str, s: pd.Series) -> bool:
    kinds = _DTYPE_KINDS.get(declared.lower())
    if kinds is None:  # "any" 또는 미지의 선언은 통과
        return True
    return s.dtype.kind in kinds


def sanitize_contract(contract: SchemaContract, available_columns) -> SchemaContract:
    """실제 입력 컬럼에 없는 입력측 요구를 제거한다.

    약한 LLM이 존재하지 않는 컬럼(심지어 'df' 같은 변수명)을 계약에 넣는
    경우가 있어, 입력측 조건은 관측 가능한 컬럼으로 한정한다.
    출력측 조건(add_columns 등)은 코드 수정 피드백에 필요하므로 유지.
    """
    cols = set(map(str, available_columns))
    contract.required_input_columns = {
        c: t for c, t in contract.required_input_columns.items() if c in cols
    }
    contract.preserve_columns = [c for c in contract.preserve_columns if c in cols]
    return contract


def verify_contract(
    contract: SchemaContract, df_in: pd.DataFrame, df_out: pd.DataFrame
) -> ContractVerificationResult:
    v: list[Violation] = []

    def critical(message: str, column: str | None = None):
        v.append(Violation(severity="critical", column=column, message=message))

    def warning(message: str, column: str | None = None):
        v.append(Violation(severity="warning", column=column, message=message))

    for col, dt in contract.required_input_columns.items():
        if col not in df_in.columns:
            critical(f"필수 입력 컬럼 '{col}' 없음", col)
        elif not _dtype_ok(dt, df_in[col]):
            warning(f"입력 컬럼 '{col}' dtype 불일치: 기대 {dt}, 실제 {df_in[col].dtype}", col)

    for col, dt in contract.add_columns.items():
        if col not in df_out.columns:
            critical(f"추가되어야 할 컬럼 '{col}' 이 출력에 없음", col)
        elif not _dtype_ok(dt, df_out[col]):
            warning(f"출력 컬럼 '{col}' dtype 불일치: 기대 {dt}, 실제 {df_out[col].dtype}", col)

    for col in contract.preserve_columns:
        if col not in df_out.columns:
            critical(f"보존되어야 할 컬럼 '{col}' 이 출력에서 사라짐", col)

    for col in contract.remove_columns:
        if col in df_out.columns:
            warning(f"제거되어야 할 컬럼 '{col}' 이 출력에 남아 있음", col)

    for c in contract.value_constraints:
        if c.column not in df_out.columns:
            critical(f"값 제약 대상 컬럼 '{c.column}' 없음", c.column)
            continue
        s = df_out[c.column]
        if c.no_nulls and bool(s.isna().any()):
            critical(f"'{c.column}' 에 null 존재 (no_nulls 위반)", c.column)
        nn = s.dropna()
        if c.min_value is not None or c.max_value is not None:
            num = pd.to_numeric(nn, errors="coerce")
            if c.min_value is not None and bool((num < c.min_value).any()):
                critical(f"'{c.column}' 최소값 {c.min_value} 미만 값 존재", c.column)
            if c.max_value is not None and bool((num > c.max_value).any()):
                critical(f"'{c.column}' 최대값 {c.max_value} 초과 값 존재", c.column)
        if c.allowed_values is not None and bool(
            (~nn.astype(str).isin(c.allowed_values)).any()
        ):
            critical(f"'{c.column}' 에 허용 목록 밖 값 존재", c.column)
        if c.regex and bool((~nn.astype(str).str.fullmatch(c.regex)).any()):
            warning(f"'{c.column}' 에 정규식 '{c.regex}' 불일치 값 존재", c.column)
        if c.unique and bool(s.duplicated().any()):
            critical(f"'{c.column}' 중복 값 존재 (unique 위반)", c.column)

    n_in, n_out = len(df_in), len(df_out)
    rel = contract.row_count
    if rel == RowCountRelation.EQUAL and n_out != n_in:
        critical(f"행 수 불일치: 입력 {n_in} → 출력 {n_out} (equal 요구)")
    elif rel == RowCountRelation.LESS_OR_EQUAL and n_out > n_in:
        critical(f"행 수 증가: 입력 {n_in} → 출력 {n_out} (less_or_equal 요구)")
    elif rel == RowCountRelation.GREATER_OR_EQUAL and n_out < n_in:
        critical(f"행 수 감소: 입력 {n_in} → 출력 {n_out} (greater_or_equal 요구)")

    has_critical = any(x.severity == "critical" for x in v)
    return ContractVerificationResult(ok=not has_critical, violations=v)
