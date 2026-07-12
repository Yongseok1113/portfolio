"""worker 로직·executor 주입 테스트 — 실제 MinIO/K8s 없이 검증."""

import io
import json

import pandas as pd

from adp_ma.ground import GroundAgentSpec, run_ladder
from adp_ma.ground.sandbox import SandboxResult
from adp_ma.ground.worker import execute_from_store


class FakeStore:
    """ArtifactStore와 동일 인터페이스의 인메모리 구현."""

    def __init__(self):
        self.data: dict[str, bytes] = {}

    def put_bytes(self, key, data):
        self.data[key] = data

    def get_bytes(self, key):
        return self.data[key]

    def exists(self, key):
        return key in self.data

    def put_df(self, key, df):
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        self.put_bytes(key, buf.getvalue())

    def get_df(self, key):
        return pd.read_parquet(io.BytesIO(self.get_bytes(key)))

    def put_text(self, key, text):
        self.put_bytes(key, text.encode())

    def get_text(self, key):
        return self.get_bytes(key).decode()

    def put_json(self, key, obj):
        self.put_bytes(key, json.dumps(obj, default=str).encode())

    def get_json(self, key):
        return json.loads(self.get_bytes(key))


def test_worker_roundtrip_success():
    store = FakeStore()
    store.put_text("r1/001/code.py", "def run(df):\n    df['b'] = df['a'] + 1\n    return df\n")
    store.put_df("r1/001/input.parquet", pd.DataFrame({"a": [1, 2, 3]}))

    status = execute_from_store(store, "r1/001")

    assert status["ok"]
    out = store.get_df("r1/001/output.parquet")
    assert list(out["b"]) == [2, 3, 4]
    assert store.get_json("r1/001/status.json")["ok"]


def test_worker_failure_writes_status_without_output():
    store = FakeStore()
    store.put_text("r1/002/code.py", "def run(df):\n    return df.boom()\n")
    store.put_df("r1/002/input.parquet", pd.DataFrame({"a": [1]}))

    status = execute_from_store(store, "r1/002")

    assert not status["ok"]
    assert "boom" in status["error"]
    assert not store.exists("r1/002/output.parquet")
    assert store.exists("r1/002/status.json")  # 실패해도 status는 반드시 남는다


def test_ladder_uses_injected_executor():
    """run_ladder가 주입된 executor(원격 실행 대체)를 실제로 사용하는지."""
    calls = []

    def fake_execute(code, df) -> SandboxResult:
        calls.append(len(df))
        out = df.copy()
        out["b"] = 0
        return SandboxResult(ok=True, df=out, elapsed_s=0.01)

    spec = GroundAgentSpec(name="t", objective="o", code="ignored")
    df = pd.DataFrame({"a": range(2000)})
    result = run_ladder(spec, df, refine=lambda s, e: s.code, execute=fake_execute)

    assert result.ok
    assert calls == [10, 100, 1000, 2000]  # XS→S→M→FULL 정확히 4회
