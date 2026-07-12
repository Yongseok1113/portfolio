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
):
    """자연어 목표로 데이터 파이프라인을 자율 구성·실행한다."""
    settings = Settings()
    if not settings.groq_api_key:
        console.print("[red]GROQ_API_KEY가 없습니다 — .env 또는 환경변수로 설정하세요[/red]")
        raise typer.Exit(2)

    from adp_ma.pipeline import PipelineRunner  # LLM 클라이언트 준비 전 import 지연

    console.print(f"[cyan]goal[/cyan]: {goal}")
    console.print(f"[cyan]input[/cyan]: {input}")
    result = PipelineRunner(settings).run(input, goal, output)

    style = "green" if result.ok else "red"
    console.print(f"\n[{style}]{'성공' if result.ok else '실패'}[/{style}]: {result.message}")
    console.print(
        f"phases={result.phases_completed} plan_retries={result.plan_retries} "
        f"llm_calls={result.llm_calls} tokens={result.total_tokens}"
    )
    console.print(f"감사 추적: {result.run_dir}/decisions.jsonl")
    if result.output_path:
        console.print(f"출력: {result.output_path}")
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
