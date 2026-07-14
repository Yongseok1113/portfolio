"""ArtifactStore 디렉토리 헬퍼·minio URI 파싱 테스트 (실 MinIO 불필요).

FakeArtifactStore는 Minio 연결(__init__)만 건너뛰고 put/get_bytes를 인메모리로
대체하므로, upload_dir·download_file 같은 실제 로직을 그대로 검증한다.
"""

import pytest

from adp_ma.state.storage import ArtifactStore, parse_minio_uri


class FakeArtifactStore(ArtifactStore):
    def __init__(self, bucket="adp-ma"):
        self.bucket = bucket
        self.data: dict[str, bytes] = {}

    def put_bytes(self, key, data):
        self.data[key] = data

    def get_bytes(self, key):
        return self.data[key]

    def exists(self, key):
        return key in self.data

    def list_prefix(self, prefix):
        return sorted(k for k in self.data if k.startswith(prefix))


def test_parse_minio_uri():
    assert parse_minio_uri("minio://adp-ma/runs/abc/output.csv") == ("adp-ma", "runs/abc/output.csv")
    assert parse_minio_uri("minio://b/k") == ("b", "k")


def test_parse_minio_uri_rejects_bad():
    with pytest.raises(ValueError):
        parse_minio_uri("s3://bucket/key")
    with pytest.raises(ValueError):
        parse_minio_uri("minio://bucketonly")


def test_upload_dir_walks_recursively(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "decisions.jsonl").write_text("line1\n", encoding="utf-8")
    (tmp_path / "agents" / "01.py").write_text("code", encoding="utf-8")

    store = FakeArtifactStore()
    n = store.upload_dir(tmp_path, "runs/xyz")

    assert n == 2
    assert store.data["runs/xyz/decisions.jsonl"] == b"line1\n"
    assert store.data["runs/xyz/agents/01.py"] == b"code"


def test_download_file_roundtrip(tmp_path):
    store = FakeArtifactStore()
    store.put_bytes("runs/xyz/output.csv", b"a,b\n1,2\n")
    dest = tmp_path / "sub" / "output.csv"
    store.download_file("runs/xyz/output.csv", dest)
    assert dest.read_bytes() == b"a,b\n1,2\n"  # 하위 디렉토리 자동 생성 포함
