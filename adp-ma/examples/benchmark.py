"""품질 벤치마크 — 고정 데이터·목표에 대해 파이프라인 산출물을 결정적 정답과 비교 채점.

사용:
  uv run python examples/benchmark.py                                  # 현재 GROQ_MODEL
  uv run python examples/benchmark.py --model llama-3.3-70b-versatile
  uv run python examples/benchmark.py --runs 3                         # 반복 측정

채점 (정답은 pandas로 결정적 계산):
  schema_ok    : region/month/total_amount 컬럼 존재 (포함 매칭 허용, 예: order_month)
  schema_exact : 요구한 컬럼명 정확 일치 여부 (지시 준수도)
  sum_rel_err  : total_amount 총합의 상대 오차 (전체 vs region 결측 제외 중 유리한 쪽)
  group_ratio  : 출력 행 수 / 기대 (region,month) 그룹 수
  pass         : schema_ok ∧ sum_rel_err ≤ 2% ∧ 0.7 ≤ group_ratio ≤ 1.3
"""

import argparse
import json
import re
import runpy
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "sales_raw.csv"

GOAL = (
    "order_id 기준으로 중복 행을 제거하라. "
    "amount('$1,234.56' 형태 문자열)를 숫자로 정규화하되, 파싱 불가·결측이어도 행은 삭제하지 말라. "
    "region을 Seoul/Busan/Incheon 세 값으로 표준화하라(대소문자 변형 통합). "
    "order_date(YYYY-MM-DD 또는 DD/MM/YYYY 혼재)를 파싱해 월(month)을 구하라. "
    "최종 출력은 region, month, total_amount 세 컬럼의 월별·지역별 매출 합계 테이블이다."
)


# ── 결정적 정답 ───────────────────────────────────────────────────────────────
def _parse_amount(v) -> float:
    if pd.isna(v):
        return float("nan")
    return float(str(v).replace("$", "").replace(",", ""))


def _parse_month(s: str) -> int:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return int(s[5:7])
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", s):  # DD/MM/YYYY
        return int(s[3:5])
    raise ValueError(f"알 수 없는 날짜 형식: {s}")


def ground_truth(raw: pd.DataFrame) -> dict:
    d = raw.drop_duplicates(subset="order_id").copy()
    d["amount_num"] = d["amount"].map(_parse_amount)
    d["region_std"] = d["region"].astype("string").str.title()
    d["month"] = d["order_date"].map(_parse_month)

    total_all = float(d["amount_num"].sum())
    regioned = d.dropna(subset=["region_std"])
    agg = regioned.groupby(["region_std", "month"])["amount_num"].sum()
    return {
        "total_all": total_all,
        "total_regioned": float(agg.sum()),
        "n_groups": int(len(agg)),
    }


# ── 채점 ─────────────────────────────────────────────────────────────────────
def _find_col(columns: list[str], target: str) -> str | None:
    """정확 일치 우선, 없으면 포함 매칭 (예: 'order_month' → 'month')."""
    norm = {c.lower().strip(): c for c in columns}
    if target in norm:
        return norm[target]
    for key, original in norm.items():
        if target in key:
            return original
    return None


def score(output_csv: str, gt: dict) -> dict:
    out = pd.read_csv(output_csv)
    targets = ("region", "month", "total_amount")
    resolved = {t: _find_col(list(out.columns), t) for t in targets}
    schema_exact = all(c is not None and c.lower().strip() == t for t, c in resolved.items())
    schema_ok = all(c is not None for c in resolved.values())

    result: dict = {"schema_ok": schema_ok, "schema_exact": schema_exact, "n_rows": len(out)}
    if not schema_ok:
        result.update(sum_rel_err=None, group_ratio=None, ok=False,
                      columns=list(out.columns))
        return result

    total_out = float(pd.to_numeric(out[resolved["total_amount"]], errors="coerce").sum())
    err = min(
        abs(total_out - gt["total_all"]) / gt["total_all"],
        abs(total_out - gt["total_regioned"]) / gt["total_regioned"],
    )
    group_ratio = len(out) / gt["n_groups"]
    result.update(
        sum_rel_err=round(err, 4),
        group_ratio=round(group_ratio, 3),
        ok=bool(err <= 0.02 and 0.7 <= group_ratio <= 1.3),
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="GROQ_MODEL 오버라이드 (예: llama-3.3-70b-versatile)")
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    if args.model:
        import os

        os.environ["GROQ_MODEL"] = args.model

    if not DATA.exists():
        runpy.run_path(str(HERE / "make_sample_data.py"))

    from adp_ma.config import Settings
    from adp_ma.pipeline import PipelineRunner

    settings = Settings()
    gt = ground_truth(pd.read_csv(DATA))
    print(f"model={settings.groq_model} | 정답: 그룹 {gt['n_groups']}개, "
          f"매출합 {gt['total_regioned']:,.0f} (region 결측 제외)\n")

    records = []
    for i in range(args.runs):
        t0 = time.monotonic()
        r = PipelineRunner(Settings()).run(DATA, GOAL)
        rec: dict = {
            "run": i + 1,
            "completed": r.ok,
            "plan_retries": r.plan_retries,
            "llm_calls": r.llm_calls,
            "tokens": r.total_tokens,
            "wall_s": round(time.monotonic() - t0, 1),
        }
        rec.update(score(r.output_path, gt) if r.ok else {"ok": False, "error": r.message[:200]})
        records.append(rec)
        print(json.dumps(rec, ensure_ascii=False))

    passed = sum(1 for r in records if r.get("ok"))
    print(f"\npass {passed}/{len(records)}")

    report = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "model": settings.groq_model,
        "ground_truth": gt,
        "runs": records,
    }
    out = Path(settings.runs_dir) / f"benchmark-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"리포트: {out}")


if __name__ == "__main__":
    main()
