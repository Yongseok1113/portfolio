"""K8sJobExecutor — ground agent 1회 실행을 adp-ma-workers 네임스페이스의
K8s Job 1개로 디스패치한다 (논문의 프로세스 격리 확보).

run_agent_code와 동일한 시그니처(callable(code, df) -> SandboxResult)라서
progressive sampling 사다리에 그대로 끼워 넣을 수 있다.
"""

import time

import pandas as pd

from adp_ma.config import Settings
from adp_ma.ground.sandbox import SandboxResult
from adp_ma.state.storage import ArtifactStore


class K8sJobExecutor:
    def __init__(self, settings: Settings, store: ArtifactStore, run_id: str):
        from kubernetes import client, config as kube_config

        try:
            kube_config.load_incluster_config()
        except kube_config.ConfigException:
            kube_config.load_kube_config()
        self._batch = client.BatchV1Api()
        self._core = client.CoreV1Api()
        self._models = client
        self.settings = settings
        self.store = store
        self.run_id = run_id.lower().replace("_", "-")
        self._seq = 0

    def __call__(self, code: str, df: pd.DataFrame) -> SandboxResult:
        self._seq += 1
        prefix = f"{self.run_id}/{self._seq:03d}"
        job_name = f"adp-ma-{self.run_id}-{self._seq:03d}"

        self.store.put_text(f"{prefix}/code.py", code)
        self.store.put_df(f"{prefix}/input.parquet", df)

        self._batch.create_namespaced_job(
            namespace=self.settings.worker_namespace, body=self._job_manifest(job_name, prefix)
        )
        try:
            infra_error = self._wait(job_name)
        finally:
            self._cleanup(job_name)

        if infra_error:
            return SandboxResult(ok=False, error=infra_error)

        if not self.store.exists(f"{prefix}/status.json"):
            return SandboxResult(
                ok=False, error=f"worker가 status.json을 남기지 않음 (job={job_name})"
            )
        status = self.store.get_json(f"{prefix}/status.json")
        out_df = self.store.get_df(f"{prefix}/output.parquet") if status["ok"] else None
        return SandboxResult(
            ok=status["ok"],
            df=out_df,
            error=status.get("error", ""),
            stdout=status.get("stdout", ""),
            elapsed_s=status.get("elapsed_s", 0.0),
            peak_mem_mb=status.get("peak_mem_mb", 0.0),
        )

    # ── Job 매니페스트 (.k8s/workers/job-template.yaml과 동일 구조) ──────────
    def _job_manifest(self, job_name: str, prefix: str):
        m = self._models
        s = self.settings
        container = m.V1Container(
            name="worker",
            image=s.worker_image,
            image_pull_policy="IfNotPresent",
            args=["worker", "--prefix", prefix],
            env=[
                # Job은 항상 클러스터 내부 MinIO 주소를 사용
                m.V1EnvVar(name="MINIO_ENDPOINT", value=s.minio_endpoint_incluster),
                m.V1EnvVar(name="MINIO_BUCKET", value=s.minio_bucket),
                # minio-secret 키가 케밥케이스(root-user)라 envFrom으론 주입 불가 — 명시 매핑
                m.V1EnvVar(
                    name="MINIO_ROOT_USER",
                    value_from=m.V1EnvVarSource(
                        secret_key_ref=m.V1SecretKeySelector(name="minio-secret", key="root-user")
                    ),
                ),
                m.V1EnvVar(
                    name="MINIO_ROOT_PASSWORD",
                    value_from=m.V1EnvVarSource(
                        secret_key_ref=m.V1SecretKeySelector(
                            name="minio-secret", key="root-password"
                        )
                    ),
                ),
            ],
            resources=m.V1ResourceRequirements(
                requests={"cpu": "100m", "memory": "256Mi"},
                limits={
                    "cpu": "1",
                    "memory": "1Gi",
                    # M4 — GPU 워크로드 옵션 (nvidia device plugin은 루트 cluster-up이 설치)
                    **({"nvidia.com/gpu": "1"} if s.worker_gpu else {}),
                },
            ),
        )
        return m.V1Job(
            metadata=m.V1ObjectMeta(
                name=job_name,
                labels={"app": "adp-ma", "role": "ground-agent", "run-id": self.run_id},
            ),
            spec=m.V1JobSpec(
                backoff_limit=0,  # 코드 오류 재시도는 refine 루프가 담당
                ttl_seconds_after_finished=600,
                active_deadline_seconds=self.settings.job_timeout_s,
                template=m.V1PodTemplateSpec(
                    metadata=m.V1ObjectMeta(labels={"app": "adp-ma", "role": "ground-agent"}),
                    spec=m.V1PodSpec(restart_policy="Never", containers=[container]),
                ),
            ),
        )

    def _wait(self, job_name: str) -> str:
        """Job 종료까지 폴링. 인프라 수준 실패 메시지를 반환 (정상이면 빈 문자열)."""
        deadline = time.monotonic() + self.settings.job_timeout_s + 60
        ns = self.settings.worker_namespace
        while time.monotonic() < deadline:
            job = self._batch.read_namespaced_job_status(job_name, ns)
            if job.status.succeeded:
                return ""
            if job.status.failed:
                return f"Job 실패 (job={job_name}): {self._pod_failure_reason(job_name)}"
            time.sleep(2)
        return f"Job 대기 시간 초과 (job={job_name})"

    def _pod_failure_reason(self, job_name: str) -> str:
        try:
            pods = self._core.list_namespaced_pod(
                self.settings.worker_namespace, label_selector=f"job-name={job_name}"
            )
            if not pods.items:
                return "pod 없음"
            pod = pods.items[0]
            logs = self._core.read_namespaced_pod_log(
                pod.metadata.name, self.settings.worker_namespace, tail_lines=60
            )
            return f"phase={pod.status.phase} logs={logs[-2500:]}"
        except Exception as e:  # 실패 원인 조회 실패는 부차적 — 최선 노력
            return f"원인 조회 실패: {e}"

    def _cleanup(self, job_name: str):
        try:
            self._batch.delete_namespaced_job(
                job_name,
                self.settings.worker_namespace,
                propagation_policy="Background",
            )
        except Exception:
            pass  # ttlSecondsAfterFinished가 최종 정리 담당
