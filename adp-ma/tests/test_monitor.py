from adp_ma.meta_agents import Monitor, StepMetrics, Verdict


def metrics(**overrides) -> StepMetrics:
    base = dict(
        rows_in=1000, rows_out=1000,
        null_rate_in=0.05, null_rate_out=0.05,
        elapsed_s=1.0, revisions=0,
    )
    base.update(overrides)
    return StepMetrics(**base)


def test_clean_run_continues():
    report = Monitor().review(metrics())
    assert report.verdict == Verdict.CONTINUE
    assert report.findings == []


def test_row_drop_critical_triggers_retry():
    report = Monitor().review(metrics(rows_out=50))  # 95% 소실
    assert report.verdict == Verdict.RETRY


def test_row_drop_warn_only_warns():
    report = Monitor().review(metrics(rows_out=600))  # 40% 감소
    assert report.verdict == Verdict.WARN


def test_row_growth_warns():
    report = Monitor().review(metrics(rows_out=7000))  # 600% 증가
    assert report.verdict == Verdict.WARN


def test_null_increase_warns():
    report = Monitor().review(metrics(null_rate_out=0.30))
    assert report.verdict == Verdict.WARN


def test_time_critical_aborts():
    report = Monitor().review(metrics(elapsed_s=301))
    assert report.verdict == Verdict.ABORT


def test_revision_critical_aborts():
    report = Monitor().review(metrics(revisions=4))
    assert report.verdict == Verdict.ABORT


def test_worst_verdict_wins():
    # WARN(행 감소) + ABORT(시간) 동시 발생 → ABORT
    report = Monitor().review(metrics(rows_out=600, elapsed_s=999))
    assert report.verdict == Verdict.ABORT
    assert len(report.findings) == 2
