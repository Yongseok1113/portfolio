"""Worker 진입 로직 — K8s Job 파드 내부에서 ground agent 코드 1개를 실행한다.

스토어 프로토콜: <prefix>/{code.py, input.parquet} 를 읽어 실행하고
<prefix>/{status.json, output.parquet(성공 시)} 를 쓴다.
실행 성패는 status.json으로 전달하므로 파드는 항상 정상 종료한다
(비정상 종료는 인프라 오류로 해석).
"""

from adp_ma.ground.sandbox import run_agent_code


def execute_from_store(store, prefix: str) -> dict:
    code = store.get_text(f"{prefix}/code.py")
    df = store.get_df(f"{prefix}/input.parquet")

    res = run_agent_code(code, df)
    if res.ok:
        store.put_df(f"{prefix}/output.parquet", res.df)

    status = {
        "ok": res.ok,
        "error": res.error,
        "stdout": res.stdout,
        "elapsed_s": res.elapsed_s,
        "peak_mem_mb": res.peak_mem_mb,
    }
    store.put_json(f"{prefix}/status.json", status)
    return status
