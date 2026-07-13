"""FastAPI web application for FindJobs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, joinedload, sessionmaker
from sqlalchemy.pool import NullPool

from findjobs.job_types import job_type_matches, split_job_types
from findjobs.locations import location_matches, split_locations
from findjobs.models import CollectRun, Company, Job, Source, UserMark
from findjobs.recommendation import recommend_from_session
from findjobs.recommendation_profile import load_recommendation_profile

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

VALID_MARK_TYPES = frozenset({"bookmark", "ignored", "applied"})

# Chinese labels for tier values shown in the recommendations UI.
_TIER_LABELS = {
    "high": "高匹配",
    "medium": "中匹配",
    "exploratory": "低匹配",
}

# Chinese labels for hard-exclusion reasons shown in the recommendations UI.
_EXCLUSION_LABELS = {
    "non_active_status": "非活跃状态",
    "non_target_relevance": "非目标相关",
    "unsupported_tags": "不支持的标签",
    "algorithm_rejection": "算法类职位",
    "huawei_exclusion": "华为排除",
    "profile_excluded_company": "个人资料排除公司",
    "missing_url": "缺少链接",
}

# Only allow redirects to /recommendations with an optional safe query string.
_SAFE_REDIRECT_RE = re.compile(r"^/recommendations(\?[a-zA-Z0-9_=&.\-]*)?$")


def _is_safe_redirect(url: str) -> bool:
    """Return True when *url* is a safe local redirect to ``/recommendations``.

    Rejects external URLs, scheme-relative URLs, and CR/LF injection.
    """
    if not url:
        return False
    # Block HTTP response splitting
    if "\r" in url or "\n" in url:
        return False
    return bool(_SAFE_REDIRECT_RE.match(url))


def is_safe_url(url: str) -> bool:
    """Return True when *url* is a safe http/https URL with nonempty netloc.

    Does not strip whitespace or normalise the stored value;
    whitespace-surrounded URLs are treated as unsafe.
    """
    if not url:
        return False
    if "\r" in url or "\n" in url:
        return False
    # Whitespace-surrounded URLs are not safe.
    if url != url.strip():
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except (ValueError, TypeError):
        return False


def _parse_tags(matched_tags: str | None) -> list[str]:
    """Parse the JSON-encoded matched_tags field of a Job."""
    if not matched_tags:
        return []
    try:
        return json.loads(matched_tags)
    except (json.JSONDecodeError, TypeError):
        return []


def _marks_summary(job: Job) -> str:
    """Return a short comma-separated summary of user marks on a job."""
    if not job.user_marks:
        return ""
    return ", ".join(m.mark_type for m in job.user_marks)


def _format_dt(dt) -> str:
    """Format a datetime to a short ISO-like string, or return empty."""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


templates.env.globals["parse_tags"] = _parse_tags
templates.env.globals["marks_summary"] = _marks_summary
templates.env.globals["fmt_dt"] = _format_dt
templates.env.globals["is_safe_url"] = is_safe_url


def _get_filter_options(session: Session) -> dict:
    """Return distinct values for filter dropdowns."""
    companies = session.query(Company).order_by(Company.name).all()
    location_rows = (
        session.query(Job.location)
        .filter(Job.location != "")
        .distinct()
        .order_by(Job.location)
        .all()
    )
    job_types = (
        session.query(Job.job_type)
        .filter(Job.job_type != "")
        .distinct()
        .order_by(Job.job_type)
        .all()
    )
    tags_raw = (
        session.query(Job.matched_tags)
        .filter(Job.matched_tags != "")
        .distinct()
        .all()
    )
    # Collect unique tag values from JSON arrays
    tag_set: set[str] = set()
    for (row,) in tags_raw:
        try:
            tag_set.update(json.loads(row))
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "companies": companies,
        "locations": sorted(
            {
                location
                for (raw_location,) in location_rows
                for location in split_locations(raw_location)
            }
        ),
        "job_types": sorted(
            {
                job_type
                for (raw_job_type,) in job_types
                for job_type in split_job_types(raw_job_type)
            }
        ),
        "tags": sorted(tag_set),
    }


def create_app(db_path: str | Path | None = None,
               profile_path: str | Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Path to the SQLite database file. When ``None`` the default
                 path from :func:`findjobs.paths.get_default_db_path` is used.
        profile_path: Path to the recommendation profile file.  When ``None``
                      the default ``<project_root>/profile/profile.md`` is used.

    Returns:
        A configured :class:`FastAPI` instance ready to serve.
    """
    if db_path is None:
        from findjobs.paths import get_default_db_path

        db_path = get_default_db_path()

    db_path = Path(db_path)
    engine = create_engine(f"sqlite:///{db_path}", echo=False, poolclass=NullPool)
    SessionLocal = sessionmaker(bind=engine)

    if profile_path is None:
        from findjobs.paths import get_project_root

        profile_path = get_project_root() / "profile" / "profile.md"
    resolved_profile = Path(profile_path)

    app = FastAPI(title="FindJobs")

    if _STATIC_DIR.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=str(_STATIC_DIR)),
            name="static",
        )

    # ---- helpers ----

    def _db() -> Session:
        return SessionLocal()

    def _load_profile():
        """Load recommendation profile, raising on missing/invalid."""
        return load_recommendation_profile(resolved_profile)

    # ---- GET /recommendations ----

    @app.get("/recommendations")
    def recommendations(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
    ):
        session = _db()
        try:
            try:
                profile = _load_profile()
            except FileNotFoundError:
                return templates.TemplateResponse(
                    request,
                    "recommendations.html",
                    {"error_empty": "未找到个人资料文件，请先创建个人资料。"},
                )
            except ValueError:
                return templates.TemplateResponse(
                    request,
                    "recommendations.html",
                    {"error_empty": "个人资料格式无效，请检查后重试。"},
                )

            result = recommend_from_session(session, profile, limit=limit)

            # Eagerly materialise UserMark summaries for returned job IDs
            job_ids = [r.job_id for r in result.recommendations]
            marks_lookup: dict[int, tuple[tuple[str, str], ...]] = {}
            if job_ids:
                rows = (
                    session.query(UserMark)
                    .filter(UserMark.job_id.in_(job_ids))
                    .order_by(UserMark.mark_type)
                    .all()
                )
                tmp: dict[int, list[tuple[str, str]]] = {}
                for m in rows:
                    tmp.setdefault(m.job_id, []).append((m.mark_type, m.note))
                marks_lookup = {k: tuple(v) for k, v in tmp.items()}

            return templates.TemplateResponse(
                request,
                "recommendations.html",
                {
                    "result": result,
                    "marks": marks_lookup,
                    "tier_labels": _TIER_LABELS,
                    "exclusion_labels": _EXCLUSION_LABELS,
                },
            )
        finally:
            session.close()

    # ---- GET / ----

    @app.get("/")
    def index():
        return RedirectResponse(url="/jobs")

    # ---- GET /jobs ----

    @app.get("/jobs")
    def jobs_list(
        request: Request,
        q: Optional[str] = Query(None),
        company: Optional[str] = Query(None),
        location: Optional[str] = Query(None),
        job_type: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        salary_disclosed: Optional[str] = Query(None),
        mark_type: Optional[str] = Query(None),
    ):
        session = _db()
        try:
            query = session.query(Job).options(
                joinedload(Job.company), joinedload(Job.user_marks)
            )

            if q:
                query = query.filter(Job.title.ilike(f"%{q}%"))
            if company:
                query = query.join(Job.company).filter(Company.slug == company)
            if tag:
                query = query.filter(Job.matched_tags.ilike(f"%{tag}%"))
            if status:
                query = query.filter(Job.status == status)
            if salary_disclosed and salary_disclosed.strip():
                val = salary_disclosed.lower() == "true"
                query = query.filter(Job.salary_disclosed == val)
            if mark_type:
                query = query.filter(
                    Job.user_marks.any(UserMark.mark_type == mark_type)
                )

            query = query.order_by(Job.last_seen_at.desc())
            jobs = query.all()
            if job_type:
                jobs = [
                    job for job in jobs if job_type_matches(job.job_type, job_type)
                ]
            if location:
                jobs = [
                    job for job in jobs if location_matches(job.location, location)
                ]
            filter_opts = _get_filter_options(session)

            return templates.TemplateResponse(
                request,
                "jobs_list.html",
                {
                    "jobs": jobs,
                    "filters": filter_opts,
                    "current": {
                        "q": q or "",
                        "company": company or "",
                        "location": location or "",
                        "job_type": job_type or "",
                        "tag": tag or "",
                        "status": status or "",
                        "salary_disclosed": salary_disclosed or "",
                        "mark_type": mark_type or "",
                    },
                },
            )
        finally:
            session.close()

    # ---- GET /jobs/{job_id} ----

    @app.get("/jobs/{job_id}")
    def job_detail(request: Request, job_id: int):
        session = _db()
        try:
            job = (
                session.query(Job)
                .options(
                    joinedload(Job.company),
                    joinedload(Job.source),
                    joinedload(Job.observations),
                    joinedload(Job.user_marks),
                )
                .filter(Job.id == job_id)
                .first()
            )
            if job is None:
                from fastapi.responses import HTMLResponse

                return HTMLResponse("Job not found", status_code=404)

            return templates.TemplateResponse(
                request,
                "job_detail.html",
                {"job": job},
            )
        finally:
            session.close()

    # ---- POST /jobs/{job_id}/marks ----

    @app.post("/jobs/{job_id}/marks")
    def set_mark(
        request: Request,
        job_id: int,
        mark_type: str = Form(...),
        note: str = Form(""),
        next_url: str = Form(""),
    ):
        if mark_type not in VALID_MARK_TYPES:
            from fastapi.responses import HTMLResponse

            return HTMLResponse(
                f"Unsupported mark_type: {mark_type!r}", status_code=400
            )

        session = _db()
        try:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job is None:
                from fastapi.responses import HTMLResponse

                return HTMLResponse("Job not found", status_code=404)

            existing = (
                session.query(UserMark)
                .filter(
                    UserMark.job_id == job_id,
                    UserMark.mark_type == mark_type,
                )
                .first()
            )
            if existing:
                existing.note = note
            else:
                mark = UserMark(job_id=job_id, mark_type=mark_type, note=note)
                session.add(mark)

            session.commit()

            if _is_safe_redirect(next_url):
                return RedirectResponse(url=next_url, status_code=303)
            return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
        finally:
            session.close()

    # ---- GET /runs ----

    @app.get("/runs")
    def runs_list(request: Request):
        session = _db()
        try:
            runs = (
                session.query(CollectRun)
                .options(joinedload(CollectRun.source))
                .order_by(CollectRun.started_at.desc())
                .all()
            )
            return templates.TemplateResponse(
                request,
                "runs.html",
                {"runs": runs},
            )
        finally:
            session.close()

    return app


# Module-level instance for development convenience.
app = create_app()
