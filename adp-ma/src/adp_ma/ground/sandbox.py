"""생성 코드 실행 샌드박스.

논문은 프로세스 격리를 사용하지만 MVP는 단일 프로세스 안에서
네임스페이스 격리(import 화이트리스트, 파일/네트워크 차단)로 축소한다.
프로세스 격리는 K8s Job(worker) 단계에서 확보하는 것이 로드맵.
"""

import builtins
import contextlib
import io
import time
import tracemalloc
import traceback
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_ALLOWED_IMPORTS = {
    "pandas",
    "numpy",
    "math",
    "re",
    "datetime",
    "json",
    "statistics",
    "collections",
    "itertools",
    "functools",
}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in _ALLOWED_IMPORTS:
        raise ImportError(f"허용되지 않은 import: {name}")
    return __import__(name, globals, locals, fromlist, level)


def _blocked(*_args, **_kwargs):
    raise RuntimeError("샌드박스에서 차단된 호출입니다 (파일/프로세스/동적실행 금지)")


def _make_namespace() -> dict:
    safe_builtins = dict(vars(builtins))
    safe_builtins["__import__"] = _safe_import
    for name in ("open", "exec", "eval", "input", "compile", "breakpoint", "exit", "quit"):
        safe_builtins[name] = _blocked
    return {"__builtins__": safe_builtins, "pd": pd, "np": np}


@dataclass
class SandboxResult:
    ok: bool
    df: pd.DataFrame | None = None
    error: str = ""
    stdout: str = ""
    elapsed_s: float = 0.0
    peak_mem_mb: float = 0.0


def run_agent_code(code: str, df: pd.DataFrame) -> SandboxResult:
    """code를 실행해 run(df)를 호출하고 결과 DataFrame을 돌려준다."""
    ns = _make_namespace()
    buf = io.StringIO()
    out: pd.DataFrame | None = None
    error = ""
    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<ground-agent>", "exec"), ns)  # noqa: S102 — 샌드박스 네임스페이스
            fn = ns.get("run")
            if not callable(fn):
                raise ValueError("생성 코드에 run(df) 함수가 정의되어 있지 않음")
            result = fn(df.copy())
        if not isinstance(result, pd.DataFrame):
            raise TypeError(
                f"run(df)는 DataFrame을 반환해야 함 (실제: {type(result).__name__})"
            )
        out = result
    except Exception:
        error = traceback.format_exc(limit=5)
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return SandboxResult(
        ok=error == "",
        df=out,
        error=error,
        stdout=buf.getvalue()[:2000],
        elapsed_s=time.perf_counter() - t0,
        peak_mem_mb=peak / 2**20,
    )
