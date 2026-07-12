"""Ground agent 정의 — Architect가 동적으로 생성·수정·폐기하는 실행 단위."""

from pydantic import BaseModel, Field

from adp_ma.contracts import SchemaContract

# 논문의 ground agent 유형. MVP는 전부 "pandas 코드 생성-실행"으로 동작이 같고,
# 유형은 Architect의 프롬프트 컨텍스트와 라이브러리 분류에 사용된다.
AGENT_TYPES = (
    "reader",
    "transformer",
    "validator",
    "joiner",
    "indexer",
    "partitioner",
    "graph",
)


class GroundAgentSpec(BaseModel):
    name: str
    agent_type: str = "transformer"
    objective: str
    # 백트래킹 시 Architect가 주입하는 수정 지침
    hints: str = ""
    contract: SchemaContract = Field(default_factory=SchemaContract)
    # 생성된 코드: `def run(df: pd.DataFrame) -> pd.DataFrame` 를 정의해야 한다
    code: str = ""
