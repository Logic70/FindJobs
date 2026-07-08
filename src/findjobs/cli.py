"""Typer CLI application for FindJobs."""

from pathlib import Path
from typing import Callable

import typer

from findjobs.adapters import AdapterContext, get_adapter
from findjobs.config import load_sources
from findjobs.paths import get_project_root

app = typer.Typer()
schedule_app = typer.Typer()
analyze_app = typer.Typer()
profile_app = typer.Typer()
app.add_typer(schedule_app, name="schedule", help="Manage scheduled collection.")
app.add_typer(analyze_app, name="analyze", help="Run local analysis workflows.")
app.add_typer(profile_app, name="profile", help="Manage the local matching profile.")


def _shorten_error(value: str | None, limit: int = 80) -> str:
    """Return a compact one-line error summary for CLI status output."""
    text = " ".join((value or "").split())
    if not text:
        return "-"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_run_dt(value) -> str:
    """Format a collect-run timestamp for CLI status output."""
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M")


def _quote_powershell_string(value: str) -> str:
    """Quote a value as a single PowerShell string literal."""
    return "'" + value.replace("'", "''") + "'"


def _scheduled_findjobs_action(
    *,
    collect_only: bool,
    db_path: str | None,
) -> str:
    """Build a Windows Task Scheduler action that can run outside this shell."""
    import shutil
    import subprocess

    uv_exe = shutil.which("uv") or "uv"
    command_parts = ["run", "findjobs"]
    if collect_only:
        command_parts.extend(["collect", "--live"])
    else:
        command_parts.extend(["weekly", "--live"])
    if db_path:
        command_parts.extend(["--db-path", db_path])

    ps_command = (
        f"Set-Location -LiteralPath {_quote_powershell_string(str(get_project_root()))}; "
        f"& {_quote_powershell_string(uv_exe)} "
        + " ".join(_quote_powershell_string(part) for part in command_parts)
    )
    return subprocess.list2cmdline(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_command,
        ]
    )


def _latest_runs_by_source_slug(db_path: str | None) -> dict[str, object]:
    """Return the latest collect run keyed by configured source slug."""
    from findjobs.db import init_db
    from findjobs.models import CollectRun, Source

    session = init_db(Path(db_path) if db_path else None)
    try:
        latest: dict[str, object] = {}
        rows = (
            session.query(CollectRun, Source.slug)
            .join(Source, CollectRun.source_id == Source.id)
            .order_by(CollectRun.started_at.desc())
            .all()
        )
        for run, source_slug in rows:
            if source_slug not in latest:
                latest[source_slug] = run
        return latest
    finally:
        session.close()


@app.command()
def init(
    db_path: str = typer.Option(
        None, "--db-path", help="Path to the SQLite database file."
    ),
):
    """Initialize the database by creating all tables."""
    from findjobs.db import init_db

    path = Path(db_path) if db_path else None
    session = init_db(path)
    session.close()
    typer.echo("Database initialized successfully.")


@profile_app.command("init")
def init_profile(
    output: str = typer.Option(
        "profile/profile.md",
        "--output",
        help="Destination profile path.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite the destination profile if it already exists.",
    ),
):
    """Create a local profile file from the example template."""
    import shutil

    from findjobs.paths import get_project_root

    root = get_project_root()
    template = root / "profile" / "profile.example.md"
    destination = Path(output)
    if not destination.is_absolute():
        destination = root / destination

    if not template.exists():
        typer.echo(f"Profile template not found: {template}", err=True)
        raise typer.Exit(1)
    if destination.exists() and not force:
        typer.echo(
            f"Profile already exists: {destination}. Use --force to overwrite.",
            err=True,
        )
        raise typer.Exit(1)

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, destination)
    typer.echo(f"Profile initialized: {destination}")


@app.command("sources")
def list_configured_sources(
    active_only: bool = typer.Option(
        False,
        "--active-only",
        help="Show only sources enabled for live collection.",
    ),
    db_path: str = typer.Option(
        None,
        "--db-path",
        help="Path to the SQLite database file used for latest run status.",
    ),
):
    """List configured company career sources and their collection status."""
    config = load_sources()
    companies = {company.slug: company for company in config.companies}
    visible_sources = [
        source for source in config.sources if source.is_active or not active_only
    ]
    active_count = sum(1 for source in config.sources if source.is_active)
    latest_runs = _latest_runs_by_source_slug(db_path)

    typer.echo(f"Configured sources: {active_count}/{len(config.sources)} active")
    for source in visible_sources:
        company = companies.get(source.company_slug)
        company_name = company.name if company is not None else source.company_slug
        status = "active" if source.is_active else "inactive"
        fetch_status = "fetch=yes" if source.fetch_url else "fetch=no"
        inactive_reason = "-"
        if not source.is_active:
            inactive_reason = _shorten_error(source.inactive_reason, limit=120)
        latest_run = latest_runs.get(source.slug)
        if latest_run is None:
            run_status = "last_status=never"
            run_started = "last_started=-"
            run_counts = "last_jobs=0 last_new=0"
            run_error = "last_error=-"
        else:
            run_status = f"last_status={latest_run.status}"
            run_started = f"last_started={_format_run_dt(latest_run.started_at)}"
            run_counts = (
                f"last_jobs={latest_run.jobs_found} last_new={latest_run.jobs_new}"
            )
            run_error = f"last_error={_shorten_error(latest_run.errors)}"
        typer.echo(
            f"{source.company_slug}\t{company_name}\t{source.slug}\t"
            f"{status}\t{source.adapter}\t{fetch_status}\t"
            f"reason={inactive_reason}\t"
            f"{run_status}\t{run_started}\t{run_counts}\t{run_error}"
        )


@app.command("adapter-audit")
def adapter_audit(
    active_only: bool = typer.Option(
        True,
        "--active-only/--all",
        help="Audit only active sources by default; use --all to include inactive.",
    ),
):
    """Print adapter quality-gate evidence for configured sources."""
    from findjobs.adapters.quality import get_quality_gate

    config = load_sources()
    sources = [
        source for source in config.sources if source.is_active or not active_only
    ]
    missing: list[str] = []

    for source in sources:
        gate = get_quality_gate(source.adapter)
        if gate is None:
            missing.append(f"{source.slug}:{source.adapter}")
            typer.echo(
                f"{source.slug}\t{source.adapter}\tquality=missing\t"
                f"active={source.is_active}"
            )
            continue

        status = "ok"
        if gate.limitations:
            status = "limited"
        checks = [
            "official" if gate.official_source else "non_official",
            "salary_facts" if gate.salary_facts_only else "salary_risk",
            "relevance" if gate.target_relevance_filtering else "relevance_missing",
            "algorithm_exclusion"
            if gate.algorithm_exclusion
            else "algorithm_exclusion_missing",
            "stable_identity" if gate.stable_identity else "identity_missing",
            "field_normalization"
            if gate.field_normalization
            else "field_normalization_missing",
        ]
        typer.echo(
            f"{source.slug}\t{source.adapter}\tquality={status}\t"
            f"active={source.is_active}\tfixture={gate.fixture}\t"
            f"pagination={gate.pagination}\tdedup={gate.deduplication}\t"
            f"detail={gate.detail_enrichment}\tchecks={','.join(checks)}"
        )

    if missing:
        typer.echo("Missing adapter quality gates: " + ", ".join(missing), err=True)
        raise typer.Exit(1)


@app.command()
def collect(
    fixture: str = typer.Option(
        None,
        "--fixture",
        help="Path to a JSON fixture file for offline collection testing.",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Perform live network collection from active sources.",
    ),
    db_path: str = typer.Option(
        None, "--db-path", help="Path to the SQLite database file."
    ),
):
    """Collect jobs from configured sources."""
    if fixture:
        _run_fixture_collect(fixture, db_path)
        return

    config = load_sources()
    active = [s for s in config.sources if s.is_active]

    if not active:
        typer.echo(
            "No active sources configured. "
            "Enable at least one source in config/sources.yaml."
        )
        return

    if not live:
        typer.echo(
            f"{len(active)} active source(s) configured. "
            "Use --live to collect."
        )
        for s in active:
            typer.echo(f"  - {s.name} ({s.slug}) via {s.adapter}")
        return

    _run_live_collect(db_path, typer.echo)


def _run_live_collect(
    db_path: str | None,
    echo: Callable[[str], None],
) -> None:
    """Run live network collection and persist jobs for active sources."""
    from findjobs.collection import (
        collect_jobs,
        complete_collect_run,
        create_collect_run,
    )
    from findjobs.db import init_db
    from findjobs.models import _utcnow
    from findjobs.repository import sync_config

    config = load_sources()
    active = [s for s in config.sources if s.is_active]
    session = init_db(Path(db_path) if db_path else None)
    maps = sync_config(session, config)

    for source_config in active:
        company = maps["companies"].get(source_config.company_slug)
        source = maps["sources"].get(source_config.slug)
        if company is None or source is None:
            echo(
                f"  {source_config.name}: company/source not synced, skipping"
            )
            continue

        try:
            adapter = get_adapter(source_config.adapter)
            context = AdapterContext(
                company_slug=source_config.company_slug,
                source_slug=source_config.slug,
                base_url=source_config.base_url,
                fetch_url=source_config.fetch_url,
            )
            echo(f"  {source_config.name}: collecting...")
            jobs = adapter.collect(context)

            run = create_collect_run(session, source.id)
            total, new_count = collect_jobs(
                session, source.id, company.id, run.id, jobs
            )
            complete_collect_run(session, run, total, new_count)
            session.commit()
            echo(
                f"  {source_config.name}: {total} jobs collected, {new_count} new"
            )
        except Exception as e:
            session.rollback()
            run = create_collect_run(session, source.id)
            run.status = "failed"
            run.finished_at = _utcnow()
            run.errors = str(e)
            session.commit()
            echo(f"  {source_config.name}: error - {e}")

    session.close()


def _run_fixture_collect(fixture_path: str, db_path: str | None) -> None:
    """Load a JSON fixture, sync config, persist jobs, and report counts."""
    import json

    from findjobs.classify import classify_job
    from findjobs.collection import (
        CollectedJob,
        collect_jobs,
        complete_collect_run,
        create_collect_run,
    )
    from findjobs.config import CompanyConfig, SourceConfig, SourcesConfig
    from findjobs.db import init_db
    from findjobs.repository import sync_config
    from findjobs.salary import parse_salary

    session = init_db(Path(db_path) if db_path else None)

    with open(fixture_path, encoding="utf-8") as f:
        data: dict = json.load(f)

    # Sync companies and sources from fixture config.
    company_configs = [CompanyConfig(**c) for c in data.get("companies", [])]
    source_configs = [SourceConfig(**s) for s in data.get("sources", [])]
    config = SourcesConfig(companies=company_configs, sources=source_configs)
    maps = sync_config(session, config)

    company = maps["companies"].get(data.get("company_slug", ""))
    source = maps["sources"].get(data.get("source_slug", ""))

    if company is None or source is None:
        typer.echo("Error: company_slug or source_slug not found in config.")
        raise typer.Exit(1)

    run = create_collect_run(session, source.id)

    collected: list[CollectedJob] = []
    for jd in data.get("jobs", []):
        salary = parse_salary(jd.get("salary_text"))
        tags = classify_job(
            jd.get("title", ""),
            jd.get("description", ""),
            jd.get("job_type", ""),
        )

        cj = CollectedJob(
            external_id=jd.get("external_id", ""),
            title=jd.get("title", ""),
            url=jd.get("url", ""),
            description=jd.get("description", ""),
            salary_text=salary["salary_text"],
            salary_min=salary["salary_min"],
            salary_max=salary["salary_max"],
            salary_currency=salary["salary_currency"],
            salary_period=salary["salary_period"],
            salary_disclosed=salary["salary_disclosed"],
            location=jd.get("location", ""),
            job_type=jd.get("job_type", ""),
            matched_tags=tags,
        )
        collected.append(cj)

    total, new_count = collect_jobs(session, source.id, company.id, run.id, collected)
    complete_collect_run(session, run, total, new_count)
    session.commit()
    session.close()

    typer.echo(f"Fixture collection complete: {total} jobs, {new_count} new.")


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Host address to bind to."
    ),
    port: int = typer.Option(
        8000, "--port", help="Port number to listen on."
    ),
    db_path: str = typer.Option(
        None, "--db-path", help="Path to the SQLite database file."
    ),
):
    """Start the web UI server."""
    import uvicorn
    from findjobs.web import create_app

    web_app = create_app(db_path=Path(db_path) if db_path else None)
    typer.echo(f"Starting FindJobs web UI at http://{host}:{port}")
    uvicorn.run(web_app, host=host, port=port)


@app.command()
def export(
    format: str = typer.Option(
        "jsonl", "--format", help="Output format: jsonl or csv."
    ),
    output: str = typer.Option(
        None, "--output", help="Output file path (stdout if omitted)."
    ),
    db_path: str = typer.Option(
        None, "--db-path", help="Path to the SQLite database file."
    ),
    since: int = typer.Option(
        None,
        "--since",
        help="Only export jobs seen within this many days.",
    ),
    tag: str = typer.Option(
        None, "--tag", help="Filter by matched tag (substring match)."
    ),
    company: str = typer.Option(
        None, "--company", help="Filter by company slug."
    ),
    status: str = typer.Option(
        None, "--status", help="Filter by job status (e.g. active, archived)."
    ),
    salary_disclosed: str = typer.Option(
        None,
        "--salary-disclosed",
        help='Filter by salary disclosure: "true" or "false".',
    ),
):
    """Export collected jobs as JSONL or CSV for AI workflow analysis.

    Exported data contains only database facts — no salary estimation or
    inferred fields.  Designed for use by external AI workflow prompts.
    """
    from pathlib import Path

    from findjobs.db import init_db
    from findjobs.exporter import do_export

    path = Path(db_path) if db_path else None
    session = init_db(path)

    sd: bool | None = None
    if salary_disclosed is not None:
        if salary_disclosed.lower() in ("true", "1", "yes"):
            sd = True
        elif salary_disclosed.lower() in ("false", "0", "no"):
            sd = False
        else:
            typer.echo(
                f"Invalid value for --salary-disclosed: '{salary_disclosed}'. "
                "Use 'true' or 'false'."
            )
            raise typer.Exit(1)

    out_stream = None
    if output:
        out_stream = Path(output).open("w", encoding="utf-8")

    try:
        result = do_export(
            session,
            fmt=format,
            output=out_stream,
            since_days=since,
            tag=tag,
            company=company,
            status=status,
            salary_disclosed=sd,
        )
    finally:
        session.close()
        if out_stream is not None:
            out_stream.close()

    if result is not None:
        typer.echo(result)


@app.command()
def prune(
    db_path: str = typer.Option(
        None, "--db-path", help="Path to the SQLite database file."
    ),
):
    """Reclassify stored jobs and delete jobs outside the AI/security scope."""
    from findjobs.db import init_db
    from findjobs.maintenance import reclassify_and_prune_irrelevant_jobs

    session = init_db(Path(db_path) if db_path else None)
    try:
        result = reclassify_and_prune_irrelevant_jobs(session)
        session.commit()
    finally:
        session.close()

    typer.echo(
        "Pruned irrelevant jobs: "
        f"scanned={result.scanned}, updated={result.updated}, deleted={result.deleted}"
    )


def _export_file(
    *,
    db_path: str | None,
    output_path: Path,
    fmt: str,
    since: int | None,
    tag: str | None = None,
) -> None:
    """Export database facts to a file for workflow consumption."""
    from findjobs.db import init_db
    from findjobs.exporter import do_export

    output_path.parent.mkdir(parents=True, exist_ok=True)
    session = init_db(Path(db_path) if db_path else None)
    with output_path.open("w", encoding="utf-8") as out_stream:
        try:
            do_export(
                session,
                fmt=fmt,
                output=out_stream,
                since_days=since,
                tag=tag,
            )
        finally:
            session.close()


@app.command()
def weekly(
    live: bool = typer.Option(
        True,
        "--live/--no-live",
        help="Run live collection before exporting and analyzing.",
    ),
    db_path: str = typer.Option(
        None, "--db-path", help="Path to the SQLite database file."
    ),
    reports_dir: str = typer.Option(
        "reports",
        "--reports-dir",
        help="Directory containing weekly/, match/, and priority/ reports.",
    ),
    profile: str = typer.Option(
        "profile/profile.md",
        "--profile",
        help="Profile file for match analysis.",
    ),
    since: int = typer.Option(
        7,
        "--since",
        help="Only export jobs seen within this many days.",
    ),
    run_date: str = typer.Option(
        None,
        "--date",
        help="Report date in YYYY-MM-DD format. Defaults to today.",
    ),
):
    """Run collect, export, and local weekly analysis as one workflow."""
    from findjobs.analysis import run_weekly_analysis

    reports = Path(reports_dir)
    weekly_dir = reports / "weekly"
    jobs_path = weekly_dir / "jobs.jsonl"
    csv_path = weekly_dir / "jobs.csv"
    ai_security_path = weekly_dir / "ai-security.jsonl"

    if live:
        typer.echo("Collecting live jobs...")
        _run_live_collect(db_path, typer.echo)
    else:
        typer.echo("Skipping live collection.")

    typer.echo("Exporting job facts...")
    _export_file(
        db_path=db_path,
        output_path=jobs_path,
        fmt="jsonl",
        since=since,
    )
    _export_file(
        db_path=db_path,
        output_path=csv_path,
        fmt="csv",
        since=since,
    )
    _export_file(
        db_path=db_path,
        output_path=ai_security_path,
        fmt="jsonl",
        since=since,
        tag="AI Security",
    )

    typer.echo("Running local analysis...")
    result = run_weekly_analysis(
        jobs_path=jobs_path,
        reports_dir=reports,
        run_date=run_date,
        profile_path=Path(profile),
    )

    typer.echo(f"Weekly workflow complete: {result.total_jobs} jobs")
    typer.echo(f"  summary: {result.summary_path}")
    typer.echo(f"  ai_security: {result.ai_security_path}")
    typer.echo(f"  manifest: {result.manifest_path}")
    if result.profile_needed_path is not None:
        typer.echo(f"  profile_needed: {result.profile_needed_path}")
    if result.matches_path is not None:
        typer.echo(f"  matches: {result.matches_path}")
    if result.priorities_path is not None:
        typer.echo(f"  priorities: {result.priorities_path}")
    if result.career_advice_path is not None:
        typer.echo(f"  career_advice: {result.career_advice_path}")


@analyze_app.command()
def weekly(
    jobs: str = typer.Option(
        "reports/weekly/jobs.jsonl",
        "--jobs",
        help="Exported jobs JSONL file to analyze.",
    ),
    reports_dir: str = typer.Option(
        "reports",
        "--reports-dir",
        help="Directory containing weekly/ and match/ report folders.",
    ),
    profile: str = typer.Option(
        "profile/profile.md",
        "--profile",
        help="Profile file for match-analysis readiness checks.",
    ),
    run_date: str = typer.Option(
        None,
        "--date",
        help="Report date in YYYY-MM-DD format. Defaults to today.",
    ),
):
    """Run the local weekly analysis workflow over exported facts."""
    from findjobs.analysis import run_weekly_analysis

    result = run_weekly_analysis(
        jobs_path=Path(jobs),
        reports_dir=Path(reports_dir),
        run_date=run_date,
        profile_path=Path(profile),
    )
    typer.echo(f"Weekly analysis complete: {result.total_jobs} jobs")
    typer.echo(f"  summary: {result.summary_path}")
    typer.echo(f"  ai_security: {result.ai_security_path}")
    typer.echo(f"  manifest: {result.manifest_path}")
    if result.profile_needed_path is not None:
        typer.echo(f"  profile_needed: {result.profile_needed_path}")
    if result.matches_path is not None:
        typer.echo(f"  matches: {result.matches_path}")
    if result.priorities_path is not None:
        typer.echo(f"  priorities: {result.priorities_path}")
    if result.career_advice_path is not None:
        typer.echo(f"  career_advice: {result.career_advice_path}")


@schedule_app.command()
def install(
    task_name: str = typer.Option(
        "FindJobsWeeklyWorkflow",
        "--task-name",
        help="Windows Task Scheduler task name.",
    ),
    time: str = typer.Option(
        "09:00", "--time", help="Time to run (HH:MM) each week."
    ),
    db_path: str = typer.Option(
        None, "--db-path", help="Path to the SQLite database file."
    ),
    collect_only: bool = typer.Option(
        False,
        "--collect-only",
        help="Schedule collect --live instead of the full weekly workflow.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Print the command without executing it.",
    ),
):
    """Install a weekly scheduled workflow via Windows Task Scheduler.

    In dry-run mode (default) the ``schtasks`` command is printed but not
    run.  Pass ``--no-dry-run`` to actually register the task (Windows only).
    """
    import subprocess

    collect_cmd_str = _scheduled_findjobs_action(
        collect_only=collect_only,
        db_path=db_path,
    )

    cmd = [
        "schtasks",
        "/create",
        "/tn",
        task_name,
        "/tr",
        collect_cmd_str,
        "/sc",
        "weekly",
        "/st",
        time,
    ]

    if dry_run:
        typer.echo(subprocess.list2cmdline(cmd))
        return

    import subprocess
    import sys

    if sys.platform != "win32":
        typer.echo("Schedule install is only supported on Windows.")
        raise typer.Exit(1)

    subprocess.run(cmd, check=True)
    typer.echo(f"Scheduled task '{task_name}' installed successfully.")


@schedule_app.command("status")
def schedule_status(
    task_name: str = typer.Option(
        "FindJobsWeeklyWorkflow",
        "--task-name",
        help="Windows Task Scheduler task name.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--no-dry-run",
        help="Print the schtasks query command without executing it.",
    ),
):
    """Show the Windows Task Scheduler status for the FindJobs task."""
    import shlex
    import subprocess
    import sys

    cmd = [
        "schtasks",
        "/query",
        "/tn",
        task_name,
        "/fo",
        "LIST",
        "/v",
    ]

    if dry_run:
        typer.echo(" ".join(shlex.quote(c) for c in cmd))
        return

    if sys.platform != "win32":
        typer.echo("Schedule status is only supported on Windows.")
        raise typer.Exit(1)

    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        typer.echo(
            f"Scheduled task '{task_name}' was not found or could not be queried."
        )
        if output:
            typer.echo(output)
        raise typer.Exit(1)

    typer.echo(output)


def run() -> None:
    """Entry point for the CLI."""
    app()
