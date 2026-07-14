"""kaggle_io 테스트 — 네트워크·실 자격증명 없이 검증 가능한 부분만."""

import json

from adp_ma import kaggle_io


def test_whoami_prefers_env(monkeypatch):
    monkeypatch.setenv("KAGGLE_USERNAME", "env_user")
    assert kaggle_io.whoami() == "env_user"


def test_whoami_falls_back_to_config(monkeypatch, tmp_path):
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    cfg = tmp_path / "kaggle.json"
    cfg.write_text(json.dumps({"username": "file_user", "key": "x"}))
    monkeypatch.setenv("KAGGLE_CONFIG_DIR", str(tmp_path))
    assert kaggle_io.whoami() == "file_user"


def test_whoami_unknown_when_no_creds(monkeypatch, tmp_path):
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.setenv("KAGGLE_CONFIG_DIR", str(tmp_path))  # 빈 디렉토리
    assert kaggle_io.whoami() == "?"


def test_find_sample_submission_variants():
    from adp_ma.kaggle_io import _find_sample_submission

    assert _find_sample_submission({"sample_submission": "a.csv"}) == "a.csv"
    # Titanic: gender_submission.csv
    assert _find_sample_submission(
        {"train": "t.csv", "test": "te.csv", "gender_submission": "g.csv"}
    ) == "g.csv"
    assert _find_sample_submission({"train": "t.csv", "test": "te.csv"}) is None


def test_submit_reports_account_on_api_failure(monkeypatch):
    monkeypatch.setenv("KAGGLE_USERNAME", "acc1")

    class FakeApi:
        def competition_submit(self, *a, **k):
            raise RuntimeError("403 forbidden")

    monkeypatch.setattr(kaggle_io, "_api", lambda: FakeApi())
    res = kaggle_io.submit("titanic", "sub.csv", "msg")
    assert not res.ok
    assert res.account == "acc1"
    assert "403" in res.message


def test_submit_success(monkeypatch):
    monkeypatch.setenv("KAGGLE_USERNAME", "acc2")

    class FakeApi:
        def competition_submit(self, *a, **k):
            return None

    monkeypatch.setattr(kaggle_io, "_api", lambda: FakeApi())
    res = kaggle_io.submit("titanic", "sub.csv", "msg")
    assert res.ok and res.account == "acc2"
