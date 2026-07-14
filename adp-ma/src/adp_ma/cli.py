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
    input: str = typer.Option(..., "--input", "-i", help="입력 데이터 (로컬 경로 또는 minio://bucket/key)"),
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
    interactive: bool = typer.Option(
        False, "--interactive", help="HITL: 계획 확정 후 실행 전에 승인 요청"
    ),
    archive: bool = typer.Option(
        False, "--archive", help="실행 종료 후 case folder를 MinIO(runs/<id>/)로 업로드"
    ),
):
    """자연어 목표로 데이터 파이프라인을 자율 구성·실행한다."""
    overrides = {}
    if workflow:
        overrides["workflow"] = workflow
    if archive:
        overrides["archive_to_minio"] = True
    settings = Settings(**overrides)
    if settings.workflow not in ("dynamic", "kaggle"):
        console.print(f"[red]알 수 없는 workflow: {settings.workflow} (dynamic|kaggle)[/red]")
        raise typer.Exit(2)
    if not settings.groq_api_key:
        console.print("[red]GROQ_API_KEY가 없습니다 — .env 또는 환경변수로 설정하세요[/red]")
        raise typer.Exit(2)

    from adp_ma.pipeline import PipelineRunner  # LLM 클라이언트 준비 전 import 지연

    console.print(f"[cyan]goal[/cyan]: {goal}")
    console.print(f"[cyan]input[/cyan]: {input}")
    runner = PipelineRunner(settings)
    if interactive:
        def review_plan(phases) -> bool:
            console.print("\n[bold]실행 계획[/bold]")
            for i, p in enumerate(phases, 1):
                console.print(f"  {i}. [{p.kind}] {p.name} — {p.objective[:80]}")
            return typer.confirm("이 계획으로 실행할까요?", default=True)

        runner.plan_reviewer = review_plan

    result = runner.run(
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
    if result.archived_to:
        console.print(f"아카이브: {result.archived_to}")
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


@app.command()
def kaggle(
    competition: str = typer.Option(..., "--competition", "-c", help="대회 slug (예: titanic)"),
    goal: str = typer.Option(..., "--goal", "-g", help="자연어 처리 목표"),
    data_dir: Path = typer.Option(Path("examples/kaggle_dl"), "--data-dir", help="다운로드 위치"),
    target: Optional[str] = typer.Option(None, "--target", help="target 컬럼명"),
    submit: bool = typer.Option(
        False, "--submit", help="[외부 공개] 채점 후 대회에 실제 제출 (확인 프롬프트)"
    ),
    yes: bool = typer.Option(False, "--yes", help="제출 확인 프롬프트 자동 승인 (자동화용)"),
):
    """Kaggle 대회 데이터 다운로드 → 파이프라인 → submission 생성 → (선택) 제출."""
    from adp_ma import kaggle_io
    from adp_ma.pipeline import PipelineRunner

    settings = Settings(workflow="kaggle")
    if not settings.groq_api_key:
        console.print("[red]GROQ_API_KEY가 없습니다[/red]")
        raise typer.Exit(2)

    console.print(f"[cyan]대회[/cyan]: {competition}  [cyan]인증 계정[/cyan]: {kaggle_io.whoami()}")
    files = kaggle_io.download_competition(competition, data_dir)
    if not (files["train"] and files["test"] and files["sample_submission"]):
        console.print(f"[red]필수 파일 누락[/red]: {files}")
        raise typer.Exit(1)

    result = PipelineRunner(settings).run(
        files["train"], goal,
        test_data=files["test"], sample_submission=files["sample_submission"], target=target,
    )
    style = "green" if result.ok else "red"
    console.print(f"\n[{style}]{'성공' if result.ok else '실패'}[/{style}]: {result.message}")
    if not result.ok or not result.submission_path:
        raise typer.Exit(1)
    console.print(f"submission: {result.submission_path} (best={result.best_model}, cv={result.cv_score})")

    if not submit:
        console.print("[yellow]제출 생략[/yellow] — 실제 제출하려면 --submit")
        raise typer.Exit(0)

    # 외부 공개 동작 — 계정·대회 명시 후 확인
    account = kaggle_io.whoami()
    console.print(f"\n[bold red]대회 '{competition}' 에 계정 '{account}' 로 제출합니다.[/bold red]")
    if not yes and not typer.confirm("제출할까요?", default=False):
        console.print("제출 취소")
        raise typer.Exit(0)

    sub = kaggle_io.submit(competition, result.submission_path, f"adp-ma {result.best_model}")
    console.print(f"[{'green' if sub.ok else 'red'}]{sub.message}[/] (계정 {sub.account})")
    if sub.ok:
        console.print(f"public score: {kaggle_io.latest_score(competition) or '채점 대기'}")
    raise typer.Exit(0 if sub.ok else 1)


if __name__ == "__main__":
    app()
