"""ADP-MA CLI — `adp-ma run --input data.csv --goal "..."`"""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from adp_ma.config import Settings

app = typer.Typer(help="ADP-MA — Autonomous Data Processing using Meta-Agents")
console = Console()


@app.command()
def run(
    input: Path = typer.Option(..., "--input", "-i", exists=True, help="입력 데이터 (csv/parquet/json)"),
    goal: str = typer.Option(..., "--goal", "-g", help="자연어 처리 목표"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="출력 경로 (기본: runs/<id>/output.csv)"),
    workflow: Optional[str] = typer.Option(
        None, "--workflow", "-w", help="dynamic(기본) | kaggle(고정 6-phase)"
    ),
    task_doc: Optional[Path] = typer.Option(
        None, "--task-doc", exists=True, help="과제 문서(markdown) — Reader가 task brief 생성에 사용"
    ),
    test_data: Optional[Path] = typer.Option(
        None, "--test-data", exists=True, help="test 데이터 — 지정 시 모델링 phase 활성 (kaggle 워크플로)"
    ),
    sample_submission: Optional[Path] = typer.Option(
        None, "--sample-submission", exists=True, help="제출 양식 csv (첫 컬럼=id, 둘째=target)"
    ),
    target: Optional[str] = typer.Option(
        None, "--target", help="target 컬럼명 (기본: sample_submission 둘째 컬럼)"
    ),
):
    """자연어 목표로 데이터 파이프라인을 자율 구성·실행한다."""
    settings = Settings(**({"workflow": workflow} if workflow else {}))
    if settings.workflow not in ("dynamic", "kaggle"):
        console.print(f"[red]알 수 없는 workflow: {settings.workflow} (dynamic|kaggle)[/red]")
        raise typer.Exit(2)
    if not settings.groq_api_key:
        console.print("[red]GROQ_API_KEY가 없습니다 — .env 또는 환경변수로 설정하세요[/red]")
        raise typer.Exit(2)

    from adp_ma.pipeline import PipelineRunner  # LLM 클라이언트 준비 전 import 지연

    console.print(f"[cyan]goal[/cyan]: {goal}")
    console.print(f"[cyan]input[/cyan]: {input}")
    result = PipelineRunner(settings).run(
        input, goal, output,
        task_doc=task_doc, test_data=test_data,
        sample_submission=sample_submission, target=target,
    )

    style = "green" if result.ok else "red"
    console.print(f"\n[{style}]{'성공' if result.ok else '실패'}[/{style}]: {result.message}")
    console.print(
        f"phases={result.phases_completed} plan_retries={result.plan_retries} "
        f"llm_calls={result.llm_calls} tokens={result.total_tokens}"
    )
    console.print(f"감사 추적: {result.run_dir}/decisions.jsonl")
    if result.output_path:
        console.print(f"출력: {result.output_path}")
    if result.submission_path:
        console.print(
            f"submission: {result.submission_path} "
            f"(best={result.best_model}, cv={result.cv_score})"
        )
    raise typer.Exit(0 if result.ok else 1)


@app.command()
def worker(
    prefix: str = typer.Option(..., "--prefix", help="아티팩트 스토어 키 프리픽스 (<run-id>/<seq>)"),
):
    """[K8s Job 내부용] 스토어에서 코드·데이터를 받아 ground agent 1개를 실행한다.

    실행 성패는 status.json으로 전달하므로 항상 0으로 종료한다
    (비정상 종료 = 인프라 오류로 해석됨).
    """
    from adp_ma.ground.worker import execute_from_store
    from adp_ma.state.storage import ArtifactStore

    settings = Settings()
    store = ArtifactStore.from_settings(settings)
    status = execute_from_store(store, prefix)
    console.print(f"worker done: ok={status['ok']} prefix={prefix}")


@app.command()
def profile(
    input: Path = typer.Option(..., "--input", "-i", exists=True, help="입력 데이터"),
):
    """입력 데이터의 프로파일(스키마·통계)만 출력한다."""
    from adp_ma.pipeline.runner import _read_table
    from adp_ma.profiling import profile_dataframe

    prof = profile_dataframe(_read_table(input))
    console.print_json(json.dumps(prof, ensure_ascii=False))


if __name__ == "__main__":
    app()
