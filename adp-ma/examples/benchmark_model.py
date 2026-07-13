"""모델링 벤치마크 — AutoKaggle 논문 지표(VS/CS) 모사 채점.

  VS (valid submission): submission.csv가 sample_submission 스키마와 일치하고
     (컬럼·행 수·id 집합·결측 없음) 정상 생성됐는가 (0/1)
  ANPS: 정규화 성능 — 분류(최대화 지표)는 원점수(accuracy)
  CS = 0.5 * VS + 0.5 * ANPS

사용:
  uv run python examples/benchmark_model.py [--model <id>] [--runs N]
"""

import argparse
import json
import runpy
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "kaggle"

GOAL = (
    "고객 이탈(churn) 이진 분류 과제. "
    "plan 대소문자 표준화, monthly_fee('$29.99' 문자열)를 숫자로 정규화(행 삭제 금지), "
    "signup_date(형식 혼재) 파싱 후 파생 피처 생성, train의 중복 행 제거. "
    "이후 churn을 예측해 submission을 만든다."
)


def score_submission(sub_path: str, sample: pd.DataFrame, truth: pd.DataFrame) -> dict:
    """VS(형식 유효성)와 accuracy(truth 대비) 채점."""
    try:
        sub = pd.read_csv(sub_path)
    except Exception as e:
        return {"vs": 0, "reason": f"submission 읽기 실패: {e}", "accuracy": None}

    id_col, target_col = sample.columns[0], sample.columns[1]
    if list(sub.columns) != list(sample.columns):
        return {"vs": 0, "reason": f"컬럼 불일치: {list(sub.columns)}", "accuracy": None}
    if len(sub) != len(sample):
        return {"vs": 0, "reason": f"행 수 불일치: {len(sub)} != {len(sample)}", "accuracy": None}
    if sub[target_col].isna().any():
        return {"vs": 0, "reason": "예측값에 결측 존재", "accuracy": None}
    if set(sub[id_col]) != set(sample[id_col]):
        return {"vs": 0, "reason": "id 집합 불일치", "accuracy": None}

    merged = truth.merge(sub, on=id_col, suffixes=("_true", "_pred"))
    acc = float((merged[f"{target_col}_true"] == merged[f"{target_col}_pred"]).mean())
    return {"vs": 1, "reason": "", "accuracy": round(acc, 4)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="GROQ_MODEL 오버라이드")
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    if args.model:
        import os

        os.environ["GROQ_MODEL"] = args.model
    if not (DATA / "train.csv").exists():
        runpy.run_path(str(HERE / "make_kaggle_data.py"))

    from adp_ma.config import Settings
    from adp_ma.pipeline import PipelineRunner

    settings = Settings()
    settings.workflow = "kaggle"
    sample = pd.read_csv(DATA / "sample_submission.csv")
    truth = pd.read_csv(DATA / "truth.csv")
    majority = float((truth[truth.columns[1]] == truth[truth.columns[1]].mode()[0]).mean())
    print(f"model={settings.groq_model} | 다수결 기준선 accuracy={majority:.3f}\n")

    records = []
    for i in range(args.runs):
        t0 = time.monotonic()
        r = PipelineRunner(settings).run(
            DATA / "train.csv", GOAL,
            test_data=DATA / "test.csv",
            sample_submission=DATA / "sample_submission.csv",
        )
        rec: dict = {
            "run": i + 1, "completed": r.ok, "best_model": r.best_model,
            "cv_score": r.cv_score, "llm_calls": r.llm_calls, "tokens": r.total_tokens,
            "wall_s": round(time.monotonic() - t0, 1),
        }
        if r.ok and r.submission_path:
            s = score_submission(r.submission_path, sample, truth)
            anps = s["accuracy"] or 0.0  # 최대화 지표 → 원점수
            rec.update(s, cs=round(0.5 * s["vs"] + 0.5 * anps, 4),
                       beats_baseline=(s["accuracy"] or 0) > majority)
        else:
            rec.update(vs=0, accuracy=None, cs=0.0, reason=r.message[:200])
        records.append(rec)
        print(json.dumps(rec, ensure_ascii=False))

    report = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "model": settings.groq_model,
        "baseline_accuracy": majority,
        "runs": records,
    }
    out = Path(settings.runs_dir) / f"benchmark-model-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n리포트: {out}")


if __name__ == "__main__":
    main()
