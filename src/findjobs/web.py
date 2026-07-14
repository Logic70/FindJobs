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

from findjobs.exporter import query_jobs
from findjobs.job_types import job_type_matches, split_job_types
from findjobs.locations import location_matches, split_locations
from findjobs.models import CollectRun, Company, Job, Source, UserMark
from findjobs.recommendation import recommend_jobs
from findjobs.recommendation_profile import load_recommendation_profile

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

VALID_MARK_TYPES = frozenset({"bookmark", "ignored", "applied"})
_VALID_STATUSES = frozenset({"all", "active", "missing", "archived"})
_VALID_RELEVANCE_STATUSES = frozenset({"all", "target", "review", "excluded"})

# Chinese labels for user mark types shown in templates.
_MARK_LABELS = {
    "bookmark": "收藏",
    "applied": "已投递",
    "ignored": "已忽略",
}

# Deterministic display order for user mark types.
_MARK_ORDER = {
    "bookmark": 0,
    "applied": 1,
    "ignored": 2,
}


def _mark_label(mark_type: str) -> str:
    """Return the Chinese display label for a mark type."""
    return _MARK_LABELS.get(mark_type, mark_type)


def _format_percent(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


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

# Safe character patterns for query keys and values in redirect URLs.
# Keys: alphanumeric, _, -, ., /, %.
# Values: same plus +.
_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9_\-./%]+$")
_SAFE_VALUE_RE = re.compile(r"^[a-zA-Z0-9_\-./%+]+$")


def _validate_percent_encoding(s: str) -> bool:
    """Return True when *s* contains no malformed percent-encoded characters.

    Checks that each ``%`` is followed by two valid hex digits and that the
    decoded byte is not a control character (0x00–0x1F or 0x7F).
    """
    i = 0
    while i < len(s):
        if s[i] == "%":
            if i + 2 >= len(s):
                return False  # truncated percent sequence
            hex_chars = s[i + 1 : i + 3]
            if not re.match(r"^[0-9a-fA-F]{2}$", hex_chars):
                return False  # invalid hex
            code = int(hex_chars, 16)
            if code < 0x20 or code == 0x7F:
                return False  # control character
            i += 3
        else:
            i += 1
    return True


def _is_safe_query(query: str) -> bool:
    """Return True when *query* is a non-empty string of safe ``key=value`` pairs.

    Each component must have a non-empty key and value containing only safe
    characters.  Percent encoding must be well-formed and not decode to any
    control character.
    """
    if not query:
        return False
    # No empty components, no leading/trailing/double ampersand.
    if query.startswith("&") or query.endswith("&") or "&&" in query:
        return False
    for pair in query.split("&"):
        if not pair or "=" not in pair:
            return False
        key, value = pair.split("=", 1)
        if not key or not value:
            return False
        if not _SAFE_KEY_RE.match(key) or not _SAFE_VALUE_RE.match(value):
            return False
        if not _validate_percent_encoding(key):
            return False
        if not _validate_percent_encoding(value):
            return False
    return True


def _is_safe_redirect(url: str) -> bool:
    """Return True when *url* is a safe local redirect to an allowed path.

    Permitted patterns (exact, no variation):

    * ``/jobs``
    * ``/jobs?<non-empty safe key=value query>``
    * ``/jobs/<numeric id>``  *(no query allowed on detail)*
    * ``/recommendations``
    * ``/recommendations?<non-empty safe key=value query>``

    Rejects external URLs, scheme-relative URLs, fragments, backslashes,
    CR/LF (literal and percent-encoded), malformed percent sequences, and
    any path not listed above.
    """
    if not url:
        return False
    # Block HTTP response splitting (literal CR/LF).
    if "\r" in url or "\n" in url:
        return False
    # Block backslashes and fragments.
    if "\\" in url or "#" in url:
        return False
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    # Block external and scheme-relative URLs.
    if parsed.scheme or parsed.netloc:
        return False
    # Validate percent-encoding in the entire URL before checking paths.
    if not _validate_percent_encoding(url):
        return False
    # Detect explicit empty query (e.g. /jobs? or /recommendations?).
    # urlparse treats a trailing ? as empty query, so we check the raw URL.
    has_explicit_empty_query = "?" in url and not parsed.query
    if has_explicit_empty_query:
        return False

    path = parsed.path
    query = parsed.query
    if path == "/jobs":
        if not query:
            return True
        return _is_safe_query(query)
    if path == "/recommendations":
        if not query:
            return True
        return _is_safe_query(query)
    # /jobs/<numeric_id> — no query allowed.
    m = re.match(r"^/jobs/(\d+)$", path)
    if m and not query:
        return True
    return False


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


def _sort_marks(marks: list) -> list:
    """Sort user marks in deterministic display order (bookmark, applied, ignored)."""
    return sorted(marks, key=lambda m: _MARK_ORDER.get(m.mark_type, 99))


def _marks_summary(job: Job) -> str:
    """Return a short comma-separated summary of user marks on a job.

    Uses Chinese labels in the order bookmark, applied, ignored
    (收藏、已投递、已忽略).
    """
    if not job.user_marks:
        return ""
    sorted_marks = sorted(
        job.user_marks,
        key=lambda m: _MARK_ORDER.get(m.mark_type, 99),
    )
    return ", ".join(_mark_label(m.mark_type) for m in sorted_marks)


def _format_dt(dt) -> str:
    """Format a datetime to a short ISO-like string, or return empty."""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


templates.env.globals["parse_tags"] = _parse_tags
templates.env.globals["marks_summary"] = _marks_summary
templates.env.globals["fmt_dt"] = _format_dt
templates.env.globals["is_safe_url"] = is_safe_url
templates.env.globals["mark_label"] = _mark_label
templates.env.globals["sort_marks"] = _sort_marks
templates.env.globals["fmt_pct"] = _format_percent


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


def create_app(
    db_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    market_report_path: str | Path | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Path to the SQLite database file. When ``None`` the default
                 path from :func:`findjobs.paths.get_default_db_path` is used.
        profile_path: Path to the recommendation profile file.  When ``None``
                      the default ``<project_root>/profile/profile.md`` is used.
        market_report_path: Path to the generated market-analysis JSON. When
                            ``None`` the project reports directory is used.

    Returns:
        A configured :class:`FastAPI` instance ready to serve.
    """
    if db_path is None:
        from findjobs.paths import get_default_db_path

        db_path = get_default_db_path()

    db_path = Path(db_path)
    engine = create_engine(f"sqlite:///{db_path}", echo=False, poolclass=NullPool)
    SessionLocal = sessionmaker(bind=engine)

    from findjobs.paths import get_project_root

    project_root = get_project_root()
    if profile_path is None:
        profile_path = project_root / "profile" / "profile.md"
    resolved_profile = Path(profile_path)
    if market_report_path is None:
        market_report_path = project_root / "reports" / "market" / "market-analysis.json"
    resolved_market_report = Path(market_report_path)

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

    def _load_market_report() -> dict:
        try:
            report = json.loads(resolved_market_report.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("尚未生成市场分析，请先运行 findjobs market-analyze。") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("市场分析文件损坏，请重新运行 findjobs market-analyze。") from exc
        except OSError as exc:
            raise ValueError(f"无法读取市场分析文件：{exc}") from exc
        if not isinstance(report, dict) or report.get("schema_version") != 3:
            raise ValueError("市场分析文件版本不受支持，请重新生成。")
        keyword_analysis = report.get("keyword_analysis")
        if not isinstance(keyword_analysis, dict) or not isinstance(
            keyword_analysis.get("keywords"), list
        ):
            raise ValueError("市场分析文件缺少关键词数据，请重新生成。")
        return report

    def _market_report_for_web(report: dict) -> dict:
        excluded_ids = {"llm_domain"}
        keyword_analysis = report["keyword_analysis"]
        keywords = [
            {
                **item,
                "related_keywords": [
                    related
                    for related in item.get("related_keywords", [])
                    if str(related.get("id")) not in excluded_ids
                ],
            }
            for item in keyword_analysis["keywords"]
            if str(item.get("id")) not in excluded_ids
        ]
        domain_signals = [
            item
            for item in report.get("domain_signals", [])
            if str(item.get("id")) not in excluded_ids
        ]
        return {
            **report,
            "domain_signals": domain_signals,
            "keyword_analysis": {**keyword_analysis, "keywords": keywords},
        }

    @app.get("/market")
    def market(request: Request):
        try:
            report = _market_report_for_web(_load_market_report())
        except ValueError as exc:
            return templates.TemplateResponse(
                request,
                "market.html",
                {"error_empty": str(exc)},
                status_code=503,
            )
        return templates.TemplateResponse(
            request,
            "market.html",
            {"market": report},
        )

    # ---- GET /recommendations ----

    @app.get("/recommendations")
    def recommendations(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        show_ignored: bool = Query(False),
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

            # Query all eligible jobs (full detail), filter ignored IDs
            # before scoring so they do not consume the recommendation limit.
            rows = query_jobs(session, detail_level="full")

            if not show_ignored:
                ignored_ids = {
                    row[0]
                    for row in session.query(UserMark.job_id)
                    .filter(UserMark.mark_type == "ignored")
                    .all()
                }
                if ignored_ids:
                    rows = [r for r in rows if r["id"] not in ignored_ids]

            result = recommend_jobs(rows, profile, limit=limit)

            # Eagerly materialise UserMark summaries for returned job IDs.
            job_ids = [r.job_id for r in result.recommendations]
            marks_lookup: dict[int, tuple[tuple[str, str], ...]] = {}
            if job_ids:
                mark_rows = (
                    session.query(UserMark)
                    .filter(UserMark.job_id.in_(job_ids))
                    .all()
                )
                tmp: dict[int, list[tuple[str, str]]] = {}
                for m in mark_rows:
                    tmp.setdefault(m.job_id, []).append((m.mark_type, m.note))
                # Sort marks in deterministic display order:
                # bookmark (收藏), applied (已投递), ignored (已忽略).
                marks_lookup = {
                    k: tuple(
                        sorted(v, key=lambda x: _MARK_ORDER.get(x[0], 99))
                    )
                    for k, v in tmp.items()
                }

            # Build the next_url for mark forms so they preserve the
            # current recommendation page URL (including query params).
            recommendations_next = (
                f"/recommendations?{request.url.query}"
                if request.url.query
                else "/recommendations"
            )

            # Pass the effective limit to the show_ignored compact form so it
            # can be preserved when toggling the checkbox.
            filter_limit: int | None = None
            limit_str = request.query_params.get("limit")
            if limit_str:
                try:
                    filter_limit = int(limit_str)
                except (ValueError, TypeError):
                    pass

            return templates.TemplateResponse(
                request,
                "recommendations.html",
                {
                    "result": result,
                    "marks": marks_lookup,
                    "tier_labels": _TIER_LABELS,
                    "exclusion_labels": _EXCLUSION_LABELS,
                    "recommendations_next": recommendations_next,
                    "filter_limit": filter_limit,
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
        relevance_status: Optional[str] = Query(None),
        salary_disclosed: Optional[str] = Query(None),
        mark_type: Optional[str] = Query(None),
        show_ignored: Optional[bool] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=100),
    ):
        # Validate enum query parameters.
        if status is not None and status not in _VALID_STATUSES:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"detail": f"Invalid status: {status!r}"}, status_code=422
            )
        if (
            relevance_status is not None
            and relevance_status not in _VALID_RELEVANCE_STATUSES
        ):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"detail": f"Invalid relevance_status: {relevance_status!r}"},
                status_code=422,
            )

        session = _db()
        try:
            # ---- Resolve effective filter values ----
            # None → default; "all" → no filter; other → use directly
            eff_status = "active" if status is None else status
            eff_relevance_status = (
                "target" if relevance_status is None else relevance_status
            )

            # ---- SQL-compatible filters (id + location + job_type) ----
            id_query = session.query(Job.id, Job.location, Job.job_type)

            if eff_status != "all":
                id_query = id_query.filter(Job.status == eff_status)
            if eff_relevance_status != "all":
                id_query = id_query.filter(
                    Job.relevance_status == eff_relevance_status
                )

            if q:
                id_query = id_query.filter(Job.title.ilike(f"%{q}%"))
            if company:
                id_query = id_query.join(Job.company).filter(
                    Company.slug == company
                )
            if tag:
                id_query = id_query.filter(
                    Job.matched_tags.ilike(f"%{tag}%")
                )
            if salary_disclosed and salary_disclosed.strip():
                val = salary_disclosed.lower() == "true"
                id_query = id_query.filter(Job.salary_disclosed == val)

            # Mark-type filter
            if mark_type:
                id_query = id_query.filter(
                    Job.user_marks.any(UserMark.mark_type == mark_type)
                )

            # Hide ignored by default — unless explicitly asking for
            # ignored marks or showing all marks.
            if mark_type != "ignored" and show_ignored is not True:
                id_query = id_query.filter(
                    ~Job.user_marks.any(UserMark.mark_type == "ignored")
                )

            # Stable order
            id_query = id_query.order_by(
                Job.last_seen_at.desc(), Job.id.desc()
            )

            # Fetch IDs with location/job_type
            id_rows = list(id_query.all())

            # Apply Python-side filters for location and job_type
            filtered_ids: list[int] = []
            for row_id, row_location, row_job_type in id_rows:
                if location and not location_matches(
                    row_location, location
                ):
                    continue
                if job_type and not job_type_matches(
                    row_job_type, job_type
                ):
                    continue
                filtered_ids.append(row_id)

            total = len(filtered_ids)
            total_pages = max(1, (total + page_size - 1) // page_size)
            start = (page - 1) * page_size
            page_ids = filtered_ids[start : start + page_size]

            # Fetch full ORM rows for the current page
            if page_ids:
                full_rows = {
                    j.id: j
                    for j in (
                        session.query(Job)
                        .options(
                            joinedload(Job.company),
                            joinedload(Job.user_marks),
                        )
                        .filter(Job.id.in_(page_ids))
                        .all()
                    )
                }
                jobs = [full_rows[jid] for jid in page_ids if jid in full_rows]
            else:
                jobs = []

            filter_opts = _get_filter_options(session)

            # Build pagination context.
            # For out-of-range pages, Previous links to the last valid page.
            if page < 1 or page > total_pages:
                prev_page = total_pages if total_pages >= 1 else None
                next_page = None
                has_prev = total_pages >= 1
                has_next = False
            else:
                has_prev = page > 1
                has_next = page < total_pages
                prev_page = page - 1 if has_prev else None
                next_page = page + 1 if has_next else None

            pagination = {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_prev": has_prev,
                "has_next": has_next,
                "prev_page": prev_page,
                "next_page": next_page,
            }

            # Pre-build prev/next URLs via Starlette so query parameters
            # (including special characters and Chinese text) are encoded
            # correctly.  Include page_size explicitly so the per‑page
            # selector value survives pagination.
            if prev_page is not None:
                pagination["prev_url"] = str(
                    request.url.include_query_params(
                        page=prev_page, page_size=page_size
                    )
                )
            if next_page is not None:
                pagination["next_url"] = str(
                    request.url.include_query_params(
                        page=next_page, page_size=page_size
                    )
                )

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
                        "relevance_status": relevance_status or "",
                        "salary_disclosed": salary_disclosed or "",
                        "mark_type": mark_type or "",
                        "show_ignored": show_ignored,
                        "effective_status": eff_status,
                        "effective_relevance_status": eff_relevance_status,
                    },
                    "pagination": pagination,
                    "page_size": str(page_size),
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

            # Enforce mark semantics:
            #   • setting ignored removes applied but leaves bookmark
            #   • setting applied removes ignored but leaves bookmark
            #   • bookmark and applied may coexist
            #   • updating an existing mark does not duplicate it
            if mark_type == "ignored":
                applied = (
                    session.query(UserMark)
                    .filter(
                        UserMark.job_id == job_id,
                        UserMark.mark_type == "applied",
                    )
                    .first()
                )
                if applied:
                    session.delete(applied)
            elif mark_type == "applied":
                ignored = (
                    session.query(UserMark)
                    .filter(
                        UserMark.job_id == job_id,
                        UserMark.mark_type == "ignored",
                    )
                    .first()
                )
                if ignored:
                    session.delete(ignored)

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

    # ---- POST /jobs/{job_id}/marks/delete ----

    @app.post("/jobs/{job_id}/marks/delete")
    def delete_mark(
        request: Request,
        job_id: int,
        mark_type: str = Form(...),
        next_url: str = Form(""),
    ):
        """Delete a user mark for a job. Idempotent for an existing valid job."""
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

            mark = (
                session.query(UserMark)
                .filter(
                    UserMark.job_id == job_id,
                    UserMark.mark_type == mark_type,
                )
                .first()
            )
            if mark:
                session.delete(mark)

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
