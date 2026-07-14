"""Kaggle 연동 — 대회 데이터 다운로드 / submission 제출 / 점수 회수.

인증은 kaggle 라이브러리의 표준 해석을 따른다 (~/.kaggle/kaggle.json 또는
KAGGLE_USERNAME/KAGGLE_KEY 환경변수). 제출은 되돌리기 어려운 외부 공개 동작이므로
호출부에서 명시적으로만 실행할 것.
"""

from dataclasses import dataclass
from pathlib import Path


def _api():
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()
    return api


def whoami() -> str:
    """현재 자격증명이 인증되는 계정명. 실제 제출 전 계정 확인용."""
    import json
    import os

    # kaggle 라이브러리에 whoami가 없어 자격증명 출처에서 계정명을 읽는다
    if os.environ.get("KAGGLE_USERNAME"):
        return os.environ["KAGGLE_USERNAME"]
    cfg = Path(os.environ.get("KAGGLE_CONFIG_DIR", Path.home() / ".kaggle")) / "kaggle.json"
    if cfg.exists():
        return json.loads(cfg.read_text()).get("username", "?")
    return "?"


def download_competition(slug: str, dest_dir: str | Path) -> dict:
    """대회 파일 다운로드·압축 해제. 표준 파일 경로를 dict로 반환."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    api = _api()
    api.competition_download_files(slug, path=str(dest), quiet=False)

    # zip 해제 (kaggle은 <slug>.zip 하나로 내려줌)
    import zipfile

    for z in dest.glob("*.zip"):
        with zipfile.ZipFile(z) as zf:
            zf.extractall(dest)
        z.unlink()

    found = {p.stem: str(p) for p in dest.glob("*.csv")}
    return {
        "train": found.get("train"),
        "test": found.get("test"),
        "sample_submission": _find_sample_submission(found),
        "all": sorted(found.values()),
    }


def _find_sample_submission(found: dict[str, str]) -> str | None:
    """제출 양식 파일 탐지 — 대회마다 이름이 다르다
    (sample_submission / gender_submission / *submission*)."""
    for stem in ("sample_submission", "sampleSubmission"):
        if stem in found:
            return found[stem]
    # train/test가 아니면서 'submission'을 포함하는 파일
    candidates = [
        path for stem, path in found.items()
        if "submission" in stem.lower() and stem.lower() not in ("train", "test")
    ]
    return candidates[0] if candidates else None


@dataclass
class SubmitResult:
    ok: bool
    account: str
    message: str
    public_score: str | None = None


def submit(slug: str, submission_csv: str | Path, message: str) -> SubmitResult:
    """submission.csv를 대회에 제출한다. 되돌리기 어려운 외부 공개 동작 —
    호출 전 반드시 사용자 확인을 받을 것."""
    account = whoami()
    api = _api()
    try:
        api.competition_submit(str(submission_csv), message=message, competition=slug)
    except Exception as e:  # noqa: BLE001 — 제출 실패 사유를 그대로 전달
        return SubmitResult(ok=False, account=account, message=f"제출 실패: {type(e).__name__}: {e}")
    return SubmitResult(ok=True, account=account, message="제출 완료")


def latest_score(slug: str) -> str | None:
    """가장 최근 제출의 public score (채점 지연 시 None일 수 있음)."""
    api = _api()
    subs = api.competition_submissions(slug)
    for s in subs:  # 최신순
        score = getattr(s, "public_score", None) or getattr(s, "publicScore", None)
        if score:
            return str(score)
    return None
