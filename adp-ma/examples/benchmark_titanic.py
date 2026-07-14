"""실제 Kaggle 데이터셋(Titanic) 평가 — 합성 데이터가 아닌 실전 검증.

공개 미러에서 titanic.csv를 받아 로컬에 캐시하고, train/test로 분할해
(test 라벨은 truth로 보관) VS/CS를 채점한다. 채점 로직은 benchmark_model 공유.

사용:
  uv run python examples/benchmark_titanic.py [--model <id>] [--runs N]
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from benchmark_model import score_submission  # 같은 디렉토리 — 직접 실행 시 import 가능

HERE = Path(__file__).parent
DATA = HERE / "titanic"
URL = "https://raw.githubusercontent.com/datasciencedojo/datasets/master/titanic.csv"

GOAL = (
    "타이타닉 승객 생존(Survived) 이진 분류 과제. "
    "Age·Embarked 결측 보정(행 삭제 금지), Sex·Embarked 등 범주형 인코딩, "
    "Name에서 호칭(Mr/Mrs/Miss 등) 추출, Fare 등 수치형 정리, "
    "SibSp+Parch로 가족 규모 파생 등 FE 후 Survived를 예측해 submission을 만든다."
)


def prepare_data():
    """다운로드(1회) 후 train 700 / test 191 분할, truth·sample_submission 생성."""
    DATA.mkdir(exist_ok=True)
    cache = DATA / "titanic_full.csv"
    if not cache.exists():
        print(f"다운로드: {URL}")
        pd.read_csv(URL).to_csv(cache, index=False)
    df = pd.read_csv(cache)

    df = df.sample(frac=1, random_state=7).reset_index(drop=True)  # 셔플 (재현 고정)
    train, test = df.iloc[:700], df.iloc[700:]
    train.to_csv(DATA / "train.csv", index=False)
    test.drop(columns=["Survived"]).to_csv(DATA / "test.csv", index=False)
    test[["PassengerId", "Survived"]].to_csv(DATA / "truth.csv", index=False)
    pd.DataFrame({"PassengerId": test["PassengerId"], "Survived": 0}).to_csv(
        DATA / "sample_submission.csv", index=False
    )
    print(f"준비 완료: train={len(train)} test={len(test)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="GROQ_MODEL 오버라이드")
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    if args.model:
        import os

        os.environ["GROQ_MODEL"] = args.model
    if not (DATA / "train.csv").exists():
        prepare_data()

    from adp_ma.config import Settings
    from adp_ma.pipeline import PipelineRunner

    settings = Settings()
    settings.workflow = "kaggle"
    sample = pd.read_csv(DATA / "sample_submission.csv")
    truth = pd.read_csv(DATA / "truth.csv")
    majority = float((truth["Survived"] == truth["Survived"].mode()[0]).mean())
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
            anps = s["accuracy"] or 0.0
            rec.update(s, cs=round(0.5 * s["vs"] + 0.5 * anps, 4),
                       beats_baseline=(s["accuracy"] or 0) > majority)
        else:
            rec.update(vs=0, accuracy=None, cs=0.0, reason=r.message[:200])
        records.append(rec)
        print(json.dumps(rec, ensure_ascii=False))

    report = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "dataset": "titanic (datasciencedojo mirror)",
        "model": settings.groq_model,
        "baseline_accuracy": majority,
        "runs": records,
    }
    out = Path(settings.runs_dir) / f"benchmark-titanic-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n리포트: {out}")


if __name__ == "__main__":
    main()
