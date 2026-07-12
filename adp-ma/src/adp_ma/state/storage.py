"""MinIO(S3 호환) 아티팩트 저장소.

K8s Job 실행 시 controller ↔ worker 간 데이터 교환 채널:
<run-id>/<seq>/{input.parquet, code.py, status.json, output.parquet}
"""

import io
import json

import pandas as pd


class ArtifactStore:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str = "adp-ma",
    ):
        from minio import Minio  # 지연 import — local 실행 경로에서는 불필요

        secure = endpoint.startswith("https://")
        host = endpoint.replace("https://", "").replace("http://", "")
        self._client = Minio(host, access_key=access_key, secret_key=secret_key, secure=secure)
        self.bucket = bucket
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)

    @classmethod
    def from_settings(cls, settings) -> "ArtifactStore":
        return cls(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password,
            bucket=settings.minio_bucket,
        )

    # ── 원시 바이트 ──────────────────────────────────────────────────────────
    def put_bytes(self, key: str, data: bytes):
        self._client.put_object(self.bucket, key, io.BytesIO(data), length=len(data))

    def get_bytes(self, key: str) -> bytes:
        resp = self._client.get_object(self.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def exists(self, key: str) -> bool:
        from minio.error import S3Error

        try:
            self._client.stat_object(self.bucket, key)
            return True
        except S3Error:
            return False

    # ── 타입별 헬퍼 ──────────────────────────────────────────────────────────
    def put_df(self, key: str, df: pd.DataFrame):
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        self.put_bytes(key, buf.getvalue())

    def get_df(self, key: str) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self.get_bytes(key)))

    def put_text(self, key: str, text: str):
        self.put_bytes(key, text.encode("utf-8"))

    def get_text(self, key: str) -> str:
        return self.get_bytes(key).decode("utf-8")

    def put_json(self, key: str, obj):
        self.put_bytes(key, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def get_json(self, key: str):
        return json.loads(self.get_bytes(key))
