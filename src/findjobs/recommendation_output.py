"""Serialization and human-readable rendering for recommendations.

Explicit serializers for ``ScoreComponent``, ``Recommendation``, and
``RecommendationResult`` that never use ``dataclasses.asdict`` on
``MappingProxyType``.  JSON output is deterministic and contains only
public recommendation facts.  The Markdown renderer produces a concise
Chinese-language report with safely escaped content.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from findjobs.recommendation import (
    Recommendation,
    RecommendationResult,
    ScoreComponent,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

_EXCERPT_LIMIT = 200

# ---------------------------------------------------------------------------
# Escaping helpers
# ---------------------------------------------------------------------------


def _escape_inline(text: str) -> str:
    """Escape *text* for safe use in inline Markdown (headings, bullets).

    Handles backslash, backticks, emphasis markers (``*`` ``_`` ``~``),
    brackets, pipe, HTML-sensitive characters, and CR/LF (→ space) so
    stored job text cannot break the report structure.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace("`", "\\`")
    text = text.replace("*", "\\*")
    text = text.replace("_", "\\_")
    text = text.replace("~", "\\~")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace("|", "\\|")
    text = text.replace("\r\n", " ")
    text = text.replace("\r", " ")
    text = text.replace("\n", " ")
    return text


def _escape_table(text: str) -> str:
    """Escape *text* for safe use in a Markdown table cell.

    Pipes, HTML special characters, and newlines are replaced so they
    cannot break the table structure.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace("\r\n", "<br>")
    text = text.replace("\r", "<br>")
    text = text.replace("\n", "<br>")
    return text


def _escape_text_preserve_nl(text: str) -> str:
    """Escape inline Markdown but preserve newlines (converted to ``<br>``).

    Used inside blockquotes where line-break semantics are desired.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace("`", "\\`")
    text = text.replace("*", "\\*")
    text = text.replace("_", "\\_")
    text = text.replace("~", "\\~")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace("|", "\\|")
    text = text.replace("\r\n", "<br>")
    text = text.replace("\r", "<br>")
    text = text.replace("\n", "<br>")
    return text


# ---------------------------------------------------------------------------
# URL rendering
# ---------------------------------------------------------------------------

_MD_BREAKING_URL_CHARS = str.maketrans(
    {
        "(": "%28",
        ")": "%29",
        "[": "%5B",
        "]": "%5D",
        "<": "%3C",
        ">": "%3E",
        '"': "%22",
        " ": "%20",
        "\n": "",
        "\r": "",
    }
)


def _percent_encode_md_url(url: str) -> str:
    """Percent-encode characters in *url* that would break Markdown link syntax."""
    return url.translate(_MD_BREAKING_URL_CHARS)


def _render_url(url: str) -> str:
    """Render *url* as a Markdown link when http/https; otherwise escaped text.

    Uses ``urlsplit`` to validate the scheme and network location.
    Percent-encodes Markdown-breaking characters in the destination.
    Non-http(s), relative, malformed, or hostless URLs are rendered as
    escaped plain text.
    """
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and parts.netloc:
        safe_url = _percent_encode_md_url(url)
        return f"[链接]({safe_url})"
    return _escape_inline(url)


# ---------------------------------------------------------------------------
# Excerpt helper
# ---------------------------------------------------------------------------


def _excerpt(text: str, limit: int = _EXCERPT_LIMIT) -> str:
    """Return a bounded single-line blockquote excerpt from *text*.

    Empty text produces an explicit availability note (``_（未提供）_``).
    Truncated excerpts are clearly marked with an ellipsis and a Chinese
    truncation notice.  Newlines are rendered as ``<br>`` inside the one
    quoted line so multiline content cannot escape the blockquote.
    """
    if not text.strip():
        return "_（未提供）_"
    stripped = text.strip()
    if len(stripped) <= limit:
        safe = _escape_text_preserve_nl(stripped)
        return f"> {safe}"
    truncated = stripped[:limit]
    safe = _escape_text_preserve_nl(truncated)
    return (
        f"> {safe}…\n"
        f"> *（因过长已截断，此处仅显示前{limit}字符）*"
    )


# ---------------------------------------------------------------------------
# JSON serializers (no ``dataclasses.asdict`` on ``MappingProxyType``)
# ---------------------------------------------------------------------------


def _score_component_to_dict(comp: ScoreComponent) -> dict[str, Any]:
    """Convert a ``ScoreComponent`` to a JSON-safe dict (tuples → lists)."""
    return {
        "score": comp.score,
        "max_score": comp.max_score,
        "message": comp.message,
        "source_fields": list(comp.source_fields),
        "profile_fields": list(comp.profile_fields),
        "matched_terms": list(comp.matched_terms),
        "gap_terms": list(comp.gap_terms),
    }


def _recommendation_to_dict(rec: Recommendation) -> dict[str, Any]:
    """Convert a ``Recommendation`` to a JSON-safe dict."""
    return {
        "job_id": rec.job_id,
        "company_slug": rec.company_slug,
        "company_name": rec.company_name,
        "title": rec.title,
        "location": rec.location,
        "job_type": rec.job_type,
        "tags": list(rec.tags),
        "url": rec.url,
        "salary_text": rec.salary_text,
        "salary_min": rec.salary_min,
        "salary_max": rec.salary_max,
        "salary_currency": rec.salary_currency,
        "salary_period": rec.salary_period,
        "salary_disclosed": rec.salary_disclosed,
        "responsibilities": rec.responsibilities,
        "requirements": rec.requirements,
        "detail_completeness": rec.detail_completeness,
        "total_score": rec.total_score,
        "tier": rec.tier,
        "domain": _score_component_to_dict(rec.domain),
        "skills": _score_component_to_dict(rec.skills),
        "requirements_score": _score_component_to_dict(rec.requirements_score),
        "experience": _score_component_to_dict(rec.experience),
        "location_score": _score_component_to_dict(rec.location_score),
        "matched_skills": list(rec.matched_skills),
        "gaps": list(rec.gaps),
        "application_advice": rec.application_advice,
    }


def _result_to_dict(result: RecommendationResult) -> dict[str, Any]:
    """Convert a ``RecommendationResult`` to a JSON-safe dict.

    ``hard_exclusion_counts`` (a ``MappingProxyType``) is explicitly
    converted to a plain ``dict`` so ``json.dumps`` can serialise it.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "scanned": result.scanned,
        "eligible": result.eligible,
        "returned": len(result.recommendations),
        "hard_exclusion_counts": dict(result.hard_exclusion_counts),
        "aggregate_learning_advice": result.aggregate_learning_advice,
        "recommendations": [
            _recommendation_to_dict(r) for r in result.recommendations
        ],
    }


# ---------------------------------------------------------------------------
# JSON rendering
# ---------------------------------------------------------------------------


def serialize_to_json(
    result: RecommendationResult, *, indent: int = 2
) -> str:
    """Render *result* as deterministic UTF-8 JSON with sorted keys.

    The output contains a schema version, scanned/eligible/returned counts,
    hard-exclusion counts, aggregate learning advice, and all recommendations
    in engine order.  No profile data, source hashes, raw resume text,
    contact information, or generated salary estimates are included.
    """
    data = _result_to_dict(result)
    return json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Chinese Markdown rendering
# ---------------------------------------------------------------------------

_EXCLUSION_LABELS: dict[str, str] = {
    "non_active_status": "非活跃状态",
    "non_target_relevance": "非目标相关度",
    "unsupported_tags": "不支持的标签",
    "algorithm_rejection": "算法岗位排除",
    "huawei_exclusion": "华为排除",
    "profile_excluded_company": "简历排除公司",
    "missing_url": "缺少官方链接",
}


def _render_component_row(label: str, comp: ScoreComponent) -> str:
    """Render one ``ScoreComponent`` as a Markdown table row."""
    score_str = f"{comp.score:.1f}/{comp.max_score:.1f}"
    source = ", ".join(comp.source_fields) if comp.source_fields else "-"
    profile = ", ".join(comp.profile_fields) if comp.profile_fields else "-"
    matched = ", ".join(comp.matched_terms) if comp.matched_terms else "-"
    gaps = ", ".join(comp.gap_terms) if comp.gap_terms else "-"
    return (
        f"| {label} | {score_str} | {_escape_table(comp.message)} "
        f"| {_escape_table(source)} | {_escape_table(profile)} "
        f"| {_escape_table(matched)} | {_escape_table(gaps)} |"
    )


def render_to_markdown(result: RecommendationResult) -> str:
    """Render *result* as a Chinese Markdown report.

    Includes summary counts, hard-exclusion counts, aggregate learning
    advice, and each recommendation with score/tier detail, a five-row
    scoring-component table, and bounded excerpts of responsibilities
    and requirements.  All inline content and table cells are safely
    escaped.
    """
    lines: list[str] = []
    _w = lines.append

    _w("# 推荐报告")
    _w("")
    _w("## 概览")
    _w("")
    _w(f"- **扫描职位**: {result.scanned}")
    _w(f"- **合格职位**: {result.eligible}")
    _w(f"- **返回结果**: {len(result.recommendations)}")
    _w("")

    # -- Hard-exclusion counts -----------------------------------------------
    if result.hard_exclusion_counts:
        _w("## 硬排除统计")
        _w("")
        _w("| 排除类型 | 数量 |")
        _w("|----------|------|")
        for key, label in _EXCLUSION_LABELS.items():
            count = result.hard_exclusion_counts.get(key, 0)
            _w(f"| {label} | {count} |")
        _w("")

    # -- Aggregate learning advice -------------------------------------------
    if result.aggregate_learning_advice:
        _w("## 学习建议")
        _w("")
        _w(result.aggregate_learning_advice)
        _w("")

    # -- Per-recommendation details ------------------------------------------
    if not result.recommendations:
        _w("*暂无推荐结果。*")
        _w("")
        return "\n".join(lines)

    _w("## 推荐详情")
    _w("")

    for idx, rec in enumerate(result.recommendations, start=1):
        salary_display = (
            rec.salary_text
            if rec.salary_disclosed and rec.salary_text.strip()
            else "未披露"
        )
        tags_str = ", ".join(rec.tags) if rec.tags else "-"
        matched_str = ", ".join(rec.matched_skills) if rec.matched_skills else "-"
        gaps_str = ", ".join(rec.gaps) if rec.gaps else "-"

        _w(f"### {idx}. {_escape_inline(rec.title)} @ {_escape_inline(rec.company_name)}（{rec.total_score:.1f}分 - {rec.tier}）")
        _w("")
        _w(f"- **地点**: {_escape_inline(rec.location)}")
        _w(f"- **类型**: {_escape_inline(rec.job_type)}")
        _w(f"- **标签**: {_escape_inline(tags_str)}")
        _w(f"- **官方链接**: {_render_url(rec.url)}")
        _w(f"- **薪资**: {_escape_inline(salary_display)}")
        _w(f"- **详情完整度**: {_escape_inline(rec.detail_completeness)}")
        _w(f"- **匹配技能**: {_escape_inline(matched_str)}")
        _w(f"- **缺口**: {_escape_inline(gaps_str)}")
        _w(f"- **申请建议**: {_escape_inline(rec.application_advice)}")
        _w("")

        # -- Component scoring table -----------------------------------------
        _w("#### 评分明细")
        _w("")
        _w("| 维度 | 得分/满分 | 评价 | 数据来源(职位) | 数据来源(简历) | 匹配项 | 缺口 |")
        _w("|------|-----------|------|----------------|----------------|--------|------|")
        _w(_render_component_row("方向匹配", rec.domain))
        _w(_render_component_row("技能匹配", rec.skills))
        _w(_render_component_row("要求覆盖", rec.requirements_score))
        _w(_render_component_row("经验匹配", rec.experience))
        _w(_render_component_row("地点匹配", rec.location_score))
        _w("")

        # -- Responsibilities excerpt ----------------------------------------
        _w("#### 职责")
        _w("")
        _w(_excerpt(rec.responsibilities))
        _w("")

        # -- Requirements excerpt --------------------------------------------
        _w("#### 要求")
        _w("")
        _w(_excerpt(rec.requirements))
        _w("")

    return "\n".join(lines)
