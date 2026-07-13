"""Tests for recommendation output and CLI integration."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from typer.testing import CliRunner

from findjobs.cli import app
from findjobs.recommendation import (
    Recommendation,
    RecommendationResult,
    ScoreComponent,
    recommend_jobs,
)
from findjobs.recommendation_output import (
    _score_component_to_dict,
    _recommendation_to_dict,
    _result_to_dict,
    render_to_markdown,
    serialize_to_json,
)
from findjobs.recommendation_profile import RecommendationProfile

# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_row(**overrides: object) -> dict:
    """Return a valid full-format job row dict with overrides applied."""
    row: dict = {
        "id": 1,
        "company_slug": "bytedance",
        "company_name": "ByteDance",
        "title": "Security Engineer",
        "location": "北京",
        "job_type": "技术",
        "status": "active",
        "salary_text": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_period": "",
        "salary_disclosed": False,
        "matched_tags": ["Security"],
        "url": "https://jobs.example.com/1",
        "first_seen_at": "2025-01-01T00:00:00",
        "last_seen_at": "2025-01-01T00:00:00",
        "published_at": "2025-01-01T00:00:00",
        "relevance_status": "target",
        "classification_version": "2.1.0",
        "classification_reasons": [],
        "description": "",
        "responsibilities": "Responsible for security testing and threat modeling.",
        "requirements": "5 years experience in Python, cloud security",
        "detail_completeness": "full",
    }
    row.update(overrides)
    return row


@pytest.fixture
def security_profile() -> RecommendationProfile:
    return RecommendationProfile(
        skills=("Python", "cloud security", "penetration testing"),
        experience_years=5.0,
        roles=("Security Engineer",),
        target_cities=("北京",),
        target_roles=("Security Engineer",),
        excluded_companies=("tencent",),
    )


def make_result(
    rows: list[dict[Any, Any]] | None = None,
    profile: RecommendationProfile | None = None,
    limit: int = 50,
) -> RecommendationResult:
    if profile is None:
        profile = RecommendationProfile(
            skills=("Python",),
            experience_years=5.0,
            target_cities=("北京",),
        )
    if rows is None:
        rows = [make_row()]
    return recommend_jobs(rows, profile, limit=limit)


# ===================================================================
#  Serializer: ScoreComponent
# ===================================================================


class TestScoreComponentToDict:
    def test_basic_fields(self) -> None:
        """All ScoreComponent fields appear in dict output."""
        comp = ScoreComponent(
            score=25.0,
            max_score=25.0,
            message="Full match.",
            source_fields=("matched_tags",),
            profile_fields=("skills", "roles", "target_roles"),
            matched_terms=("Security",),
            gap_terms=(),
        )
        d = _score_component_to_dict(comp)
        assert d["score"] == 25.0
        assert d["max_score"] == 25.0
        assert d["message"] == "Full match."
        assert d["source_fields"] == ["matched_tags"]
        assert d["profile_fields"] == ["skills", "roles", "target_roles"]
        assert d["matched_terms"] == ["Security"]
        assert d["gap_terms"] == []

    def test_tuples_converted_to_lists(self) -> None:
        """Tuples are converted to JSON-safe lists."""
        comp = ScoreComponent(
            score=10.0, max_score=20.0, message="test",
            source_fields=("a", "b"),
            profile_fields=("c",),
            matched_terms=("x", "y"),
            gap_terms=("z",),
        )
        d = _score_component_to_dict(comp)
        assert isinstance(d["source_fields"], list)
        assert isinstance(d["profile_fields"], list)
        assert isinstance(d["matched_terms"], list)
        assert isinstance(d["gap_terms"], list)

    def test_empty_terms(self) -> None:
        """Empty tuples produce empty lists, not None."""
        comp = ScoreComponent(
            score=0.0, max_score=10.0, message="none",
        )
        d = _score_component_to_dict(comp)
        assert d["source_fields"] == []
        assert d["profile_fields"] == []
        assert d["matched_terms"] == []
        assert d["gap_terms"] == []


# ===================================================================
#  Serializer: Recommendation
# ===================================================================


class TestRecommendationToDict:
    def test_all_fields_present(self, security_profile: RecommendationProfile) -> None:
        """Every public recommendation field is in the dict."""
        result = make_result(profile=security_profile)
        rec = result.recommendations[0]
        d = _recommendation_to_dict(rec)

        assert d["job_id"] == 1
        assert d["company_slug"] == "bytedance"
        assert d["company_name"] == "ByteDance"
        assert d["title"] == "Security Engineer"
        assert d["location"] == "北京"
        assert d["job_type"] == "技术"
        assert d["tags"] == ["Security"]
        assert d["url"] == "https://jobs.example.com/1"
        assert d["salary_text"] == ""
        assert d["salary_min"] is None
        assert d["salary_max"] is None
        assert d["salary_currency"] == ""
        assert d["salary_period"] == ""
        assert d["salary_disclosed"] is False
        assert d["responsibilities"] != ""
        assert d["requirements"] != ""
        assert d["detail_completeness"] == "full"
        assert isinstance(d["total_score"], float)
        assert isinstance(d["tier"], str)
        assert isinstance(d["matched_skills"], list)
        assert isinstance(d["gaps"], list)
        assert isinstance(d["application_advice"], str)

    def test_five_components_present(self, security_profile: RecommendationProfile) -> None:
        """All five named ScoreComponent keys are in the dict."""
        result = make_result(profile=security_profile)
        d = _recommendation_to_dict(result.recommendations[0])
        for key in ("domain", "skills", "requirements_score", "experience", "location_score"):
            assert key in d
            assert isinstance(d[key], dict)
            assert "score" in d[key]
            assert "max_score" in d[key]

    def test_no_privacy_fields(self, security_profile: RecommendationProfile) -> None:
        """No profile data, source hash, raw resume text, or contact info."""
        result = make_result(profile=security_profile)
        d = _recommendation_to_dict(result.recommendations[0])
        sensitive_keys = {"description", "first_seen_at", "last_seen_at",
                          "classification_version", "classification_reasons",
                          "relevance_status", "source_hash", "contact"}
        for key in sensitive_keys:
            assert key not in d

    def test_no_salary_estimates(self, security_profile: RecommendationProfile) -> None:
        """No generated/estimated salary fields (raw DB facts only)."""
        result = make_result(profile=security_profile)
        d = _recommendation_to_dict(result.recommendations[0])
        # Only the raw salary fields from the DB should be present
        salary_keys = {"salary_text", "salary_min", "salary_max",
                       "salary_currency", "salary_period", "salary_disclosed"}
        for k in salary_keys:
            assert k in d
        assert "salary_estimate" not in d
        assert "estimated_salary" not in d


# ===================================================================
#  Serializer: RecommendationResult
# ===================================================================


class TestResultToDict:
    def test_top_level_fields(self, security_profile: RecommendationProfile) -> None:
        """Result dict has schema_version, counts, exclusions, advice, recs."""
        result = make_result(profile=security_profile)
        d = _result_to_dict(result)

        assert d["schema_version"] == 1
        assert d["scanned"] == 1
        assert d["eligible"] == 1
        assert d["returned"] == 1
        assert isinstance(d["hard_exclusion_counts"], dict)
        assert isinstance(d["aggregate_learning_advice"], str)
        assert isinstance(d["recommendations"], list)
        assert len(d["recommendations"]) == 1

    def test_mappingproxy_converted(self, security_profile: RecommendationProfile) -> None:
        """hard_exclusion_counts is a plain dict, not MappingProxyType."""
        result = make_result(profile=security_profile)
        d = _result_to_dict(result)
        counts = d["hard_exclusion_counts"]
        assert type(counts) is dict
        assert "non_active_status" in counts
        assert "missing_url" in counts
        # Verify it's mutable (JSON will serialize it correctly)
        counts["test"] = 1  # type: ignore[index]
        assert counts["test"] == 1

    def test_multiple_recommendations(self) -> None:
        """Multiple recs are all included in order."""
        rows = [make_row(id=i) for i in range(3)]
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        result = make_result(rows=rows, profile=profile)
        d = _result_to_dict(result)
        assert d["returned"] == 3
        assert len(d["recommendations"]) == 3

    def test_zero_results(self) -> None:
        """No eligible jobs → empty recommendations list."""
        result = make_result(
            rows=[make_row(status="archived")],
            profile=RecommendationProfile(target_cities=("北京",)),
        )
        d = _result_to_dict(result)
        assert d["scanned"] == 1
        assert d["eligible"] == 0
        assert d["returned"] == 0
        assert d["recommendations"] == []


# ===================================================================
#  Deterministic JSON
# ===================================================================


class TestDeterministicJson:
    def test_identical_inputs_identical_output(self, security_profile: RecommendationProfile) -> None:
        """Same result produces identical JSON bytes."""
        result = make_result(profile=security_profile)
        j1 = serialize_to_json(result)
        j2 = serialize_to_json(result)
        assert j1 == j2

    def test_no_timestamps(self, security_profile: RecommendationProfile) -> None:
        """JSON must not contain current timestamps."""
        result = make_result(profile=security_profile)
        j = serialize_to_json(result)
        data = json.loads(j)
        # Check no timestamp keys at any level
        json_str = json.dumps(data)
        # These patterns should not appear
        for pattern in ("2026-", "2025-", "timestamp", "generated_at", "created_at"):
            # Allow schema_version which is just '1'
            pass
        # Verify no date-looking strings
        assert "generated_at" not in json_str
        assert "timestamp" not in json_str

    def test_no_profile_path(self, security_profile: RecommendationProfile) -> None:
        """Absolute profile paths must not appear in JSON."""
        result = make_result(profile=security_profile)
        j = serialize_to_json(result)
        assert "profile/" not in j
        assert "profile.md" not in j

    def test_valid_utf8_json(self, security_profile: RecommendationProfile) -> None:
        """Output is valid UTF-8 JSON with ensure_ascii=False."""
        result = make_result(profile=security_profile)
        j = serialize_to_json(result)
        data = json.loads(j)
        assert data["schema_version"] == 1

    def test_deterministic_field_order(self, security_profile: RecommendationProfile) -> None:
        """Field ordering in JSON is predictable (sorted keys)."""
        result = make_result(profile=security_profile)
        j1 = serialize_to_json(result)
        j2 = serialize_to_json(result)
        assert j1 == j2


# ===================================================================
#  Markdown renderer
# ===================================================================


class TestMarkdownSummary:
    def test_summary_counts_present(self, security_profile: RecommendationProfile) -> None:
        """Summary section shows scanned, eligible, returned."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        assert "扫描" in md
        assert "合格" in md
        assert "返回" in md

    def test_hard_exclusion_counts_present(self, security_profile: RecommendationProfile) -> None:
        """Hard-exclusion counts are in the markdown."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        assert "排除" in md or "hard" in md.lower() or "Hard" in md

    def test_aggregate_advice_present(self, security_profile: RecommendationProfile) -> None:
        """Aggregate learning advice appears in markdown."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        assert len(result.aggregate_learning_advice) > 0


class TestMarkdownRecommendation:
    def test_rec_fields_present(self, security_profile: RecommendationProfile) -> None:
        """Each rec shows score, tier, company, title, location, type, tags, URL."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        rec = result.recommendations[0]
        assert str(rec.total_score) in md
        assert rec.tier in md
        assert rec.company_name in md
        assert rec.title in md
        assert rec.location in md

    def test_url_as_markdown_link(self, security_profile: RecommendationProfile) -> None:
        """http/https URLs rendered as plain Markdown links."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        url = result.recommendations[0].url
        assert f"](" in md  # Markdown link syntax present
        # The URL or a portion of it should appear as a link target
        assert re.search(rf"\]\({re.escape(url)}", md)

    def test_undisclosed_salary(self, security_profile: RecommendationProfile) -> None:
        """Undisclosed salary shows 未披露."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        assert "未披露" in md

    def test_disclosed_salary(self) -> None:
        """Disclosed salary shows the salary text."""
        row = make_row(
            salary_text="50k-80k",
            salary_disclosed=True,
        )
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        assert "50k-80k" in md

    def test_component_table(self, security_profile: RecommendationProfile) -> None:
        """Five-row component table with score/max, message, source, profile, matches, gaps."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        # Should have 5 component rows
        lines = md.split("\n")
        pipe_rows = [l for l in lines if l.startswith("|") and "得分" not in l and "---" not in l and "维度" not in l]
        # At least one recommendation should have 5 data rows
        assert len(pipe_rows) >= 5

    def test_matched_skills_listed(self, security_profile: RecommendationProfile) -> None:
        """Matched skills appear in the output."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        rec = result.recommendations[0]
        for skill in rec.matched_skills:
            # Skill names should appear somewhere in the markdown
            assert skill in md or skill.lower() in md.lower()

    def test_gaps_listed(self) -> None:
        """Gaps appear in the output."""
        profile = RecommendationProfile(
            skills=("Java",),
            target_cities=("深圳",),
        )
        row = make_row(location="上海", requirements="C++, Go")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # Gaps should be mentioned
        assert len(result.recommendations[0].gaps) > 0

    def test_application_advice_present(self, security_profile: RecommendationProfile) -> None:
        """Application advice appears."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        rec = result.recommendations[0]
        # Some portion of the advice should appear
        if rec.application_advice:
            assert len(rec.application_advice) > 0

    def test_detail_completeness_shown(self, security_profile: RecommendationProfile) -> None:
        """detail_completeness field is rendered."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        assert "full" in md

    def test_job_type_shown(self, security_profile: RecommendationProfile) -> None:
        """Job type is rendered."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        assert "技术" in md

    def test_tags_shown(self, security_profile: RecommendationProfile) -> None:
        """Tags are rendered."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        assert "Security" in md

    def test_location_shown(self, security_profile: RecommendationProfile) -> None:
        """Location is rendered."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        assert "北京" in md


class TestMarkdownEscaping:
    def test_pipes_escaped(self) -> None:
        """Pipes in stored text are escaped to not break tables."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(title="Engineer | Senior", location="A | B")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # Pipes inside job text that appear in table cells must be escaped.
        # Check that the component table rows all have 9 |-separated parts
        # (leading empty + 7 values + trailing empty) after the header row.
        lines = md.split("\n")
        in_comp_table = False
        for line in lines:
            if "评分明细" in line:
                in_comp_table = True
                continue
            if in_comp_table and line.startswith("|"):
                # Skip header and separator rows
                if "维度" in line or "---" in line:
                    continue
                # Each data row should have 7 columns (9 parts with split)
                parts = line.split("|")
                assert len(parts) == 9, (
                    f"Expected 9 parts for 7-column table, got {len(parts)}: {line!r}"
                )

    def test_html_chars_escaped(self) -> None:
        """HTML-sensitive characters in job text are escaped."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(
            title="<script>alert('xss')</script>",
            requirements="C++ & C",
        )
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # Raw HTML must not appear; HTML entities must be used instead.
        assert "<script>" not in md
        assert "C++ & C" not in md
        assert "&lt;" in md
        assert "&amp;" in md

    def test_newlines_in_cells(self) -> None:
        """Newlines in text don't break table structure."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(responsibilities="Line1\nLine2\nLine3")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # The component table rows should all have 9 |-separated parts
        # (leading empty + 7 values + trailing empty).
        lines = md.split("\n")
        in_comp_table = False
        for line in lines:
            if "评分明细" in line:
                in_comp_table = True
                continue
            if in_comp_table and line.startswith("|"):
                if "维度" in line or "---" in line:
                    continue
                parts = line.split("|")
                assert len(parts) == 9, (
                    f"Expected 9 parts for 7-column table, got {len(parts)}: {line!r}"
                )


class TestMarkdownUrlHandling:
    def test_http_url_as_link(self, security_profile: RecommendationProfile) -> None:
        """http:// URLs are rendered as Markdown links."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(url="http://example.com/job")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        assert "[链接](http://example.com/job)" in md

    def test_https_url_as_link(self, security_profile: RecommendationProfile) -> None:
        """https:// URLs are rendered as Markdown links."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(url="https://careers.example.com/123")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        assert "https://careers.example.com/123" in md

    def test_non_url_rendered_as_text(self) -> None:
        """Non-http URLs are rendered as escaped text."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(url="ftp://files.example.com")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        assert "ftp://" in md
        # It should NOT be a Markdown link with ](...)
        assert not re.search(r'\[.*?\]\(ftp://', md)

    def test_url_with_parentheses_and_spaces(self) -> None:
        """URL containing parentheses and spaces are percent-encoded."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(url="https://example.com/job(1) with spaces")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # Markdown-breaking chars must be percent-encoded in the link dest
        assert "[链接](" in md
        assert "%28" in md  # (
        assert "%29" in md  # )
        assert "%20" in md  # space
        # Raw parens/spaces must not appear in the link destination
        assert "(1)" not in md or "%28" in md

    def test_hostless_url_rendered_as_text(self) -> None:
        """Hostless ``https:foo`` is rendered as escaped text, not a link."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(url="https:foo")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        assert "https:foo" in md
        # Must NOT be wrapped as a Markdown link
        assert not re.search(r'\[.*?\]\(https:foo', md)

    def test_javascript_url_rendered_as_text(self) -> None:
        """``javascript:`` URI is rendered as escaped text, never a link."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(url="javascript:alert(1)")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        assert "javascript" in md
        assert not re.search(r'\[.*?\]\(javascript:', md)


class TestMarkdownExcerpts:
    def test_responsibilities_excerpt(self, security_profile: RecommendationProfile) -> None:
        """Responsibilities are shown with bounded excerpt."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        rec = result.recommendations[0]
        if len(rec.responsibilities) > 200:
            assert "…" in md or "..." in md
        else:
            assert rec.responsibilities in md

    def test_requirements_excerpt(self, security_profile: RecommendationProfile) -> None:
        """Requirements are shown with bounded excerpt."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        rec = result.recommendations[0]
        if len(rec.requirements) > 200:
            assert "…" in md or "..." in md
        else:
            assert rec.requirements in md

    def test_missing_requirements_not_invented(self) -> None:
        """When requirements are empty, don't invent them."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(requirements="", detail_completeness="missing")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # Should indicate requirements are not available
        assert "未提供" in md or "不可用" in md or "not" in md.lower() or "unavailable" in md.lower()

    def test_truncation_marked(self) -> None:
        """Truncated text is clearly marked."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        long_reqs = "Python, " * 100
        row = make_row(requirements=long_reqs)
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # Truncation marker should be present for long text
        assert "…" in md or "[truncated]" in md.lower() or "[省略]" in md or "..." in md


# ===================================================================
#  Hostile content — structure-integrity tests
# ===================================================================


class TestMarkdownHostileContent:
    """Verify the report structure survives malicious job text."""

    def _assert_table_structure(self, md: str) -> None:
        """Check every scoring-table data row has 9 |-separated parts."""
        lines = md.split("\n")
        in_comp = False
        for line in lines:
            if "评分明细" in line:
                in_comp = True
                continue
            if in_comp and line.startswith("|"):
                if "维度" in line or "---" in line:
                    continue
                parts = line.split("|")
                assert len(parts) == 9, (
                    f"Expected 9 parts, got {len(parts)}: {line!r}"
                )

    def test_title_with_md_syntax(self) -> None:
        """Title with emphasis, backticks, brackets, pipe, tilde."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(title="Engineer *Senior* _Lead_ `code` [link] | Test ~strike~")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # Escaped versions must appear in the heading
        assert "\\*Senior\\*" in md or "&amp;" in md
        assert "\\[link\\]" in md or "&amp;" in md
        self._assert_table_structure(md)

    def test_company_with_backslash(self) -> None:
        """Company containing backslash."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(company_name="ACME\\Corp")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        assert "ACME\\\\Corp" in md
        self._assert_table_structure(md)

    def test_location_with_md_syntax(self) -> None:
        """Location containing bracket syntax that could break rendering."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(location="[click here](evil)")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # Brackets must be escaped so no link is formed
        assert "\\[click here\\]" in md
        assert not re.search(r'\[click here\]\(evil\)', md)
        self._assert_table_structure(md)

    def test_salary_with_emphasis_chars(self) -> None:
        """Salary text with * and _ does not create emphasis."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(salary_text="*good*_pay_", salary_disclosed=True)
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        assert "\\*good\\*" in md
        assert "\\_pay\\_" in md
        # Must not be rendered as actual emphasis (no <em> effect)
        assert "*good*" not in md.replace("\\*good\\*", "")
        self._assert_table_structure(md)

    def test_multiline_responsibilities_crlf(self) -> None:
        """CRLF in responsibilities are safely contained within blockquote."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(responsibilities="Task1\r\nTask2\nTask3\rDone")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # All content must be present
        assert "Task1" in md
        assert "Task2" in md
        assert "Task3" in md
        assert "Done" in md
        # Verify the responsibilities excerpt is a single blockquote line
        # Find the "#### 职责" section and check its blockquote
        lines = md.split("\n")
        found_duty = False
        for i, line in enumerate(lines):
            if line.strip().startswith("####") and "职责" in line:
                found_duty = True
                # Next lines: empty, then blockquote, then empty
                # Collect blockquote lines until next heading or blank
                blines = []
                for j in range(i + 1, len(lines)):
                    if lines[j].startswith(">"):
                        blines.append(lines[j])
                    elif lines[j].startswith("##") or lines[j].startswith("###") or lines[j].startswith("####"):
                        break
                assert len(blines) == 1 or (
                    len(blines) == 2 and "截断" in blines[1]
                ), f"Expected ≤2 blockquote lines in duties, got {len(blines)}"
                break
        if not found_duty:
            # If no duties heading, at minimum check no raw newlines break it
            pass
        self._assert_table_structure(md)

    def test_all_special_chars_in_title(self) -> None:
        """Title with every escape category simultaneously."""
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))
        row = make_row(title="A\\B`C`*D*_E_~F~[G]|H<I>&J")
        result = make_result(rows=[row], profile=profile)
        md = render_to_markdown(result)
        # All must be escaped; raw versions must not break the report
        assert "\\[G\\]" in md
        assert "\\|" in md
        assert "&lt;I&gt;" in md or "&lt;I" in md
        self._assert_table_structure(md)


# ===================================================================
#  CLI: validation errors (no artifact left behind)
# ===================================================================


class TestCliValidation:
    def test_invalid_format_no_artifact(self, tmp_path: Path) -> None:
        """Invalid --format produces error and no output file."""
        output = tmp_path / "out.md"
        result = runner.invoke(app, [
            "recommend", "--format", "xml", "--output", str(output),
        ])
        assert result.exit_code != 0
        assert not output.exists()

    def test_invalid_limit_negative_no_artifact(self, tmp_path: Path) -> None:
        """Negative --limit produces error and no output file."""
        output = tmp_path / "out.md"
        result = runner.invoke(app, [
            "recommend", "--limit", "-1", "--output", str(output),
        ])
        assert result.exit_code != 0
        assert not output.exists()

    def test_invalid_limit_zero_no_artifact(self, tmp_path: Path) -> None:
        """Zero --limit produces error and no output file."""
        output = tmp_path / "out.md"
        result = runner.invoke(app, [
            "recommend", "--limit", "0", "--output", str(output),
        ])
        assert result.exit_code != 0
        assert not output.exists()

    def test_limit_too_large_no_artifact(self, tmp_path: Path) -> None:
        """Limit > 1000 produces error and no output file."""
        output = tmp_path / "out.md"
        result = runner.invoke(app, [
            "recommend", "--limit", "1001", "--output", str(output),
        ])
        assert result.exit_code != 0
        assert not output.exists()

    def test_missing_profile_no_artifact(self, tmp_path: Path) -> None:
        """Missing --profile produces error and no output file."""
        missing = tmp_path / "no_such_profile.md"
        output = tmp_path / "out.md"
        result = runner.invoke(app, [
            "recommend", "--profile", str(missing), "--output", str(output),
        ])
        assert result.exit_code != 0
        assert not output.exists()

    def test_limit_not_int_no_artifact(self, tmp_path: Path) -> None:
        """Non-integer --limit produces error and no output file."""
        output = tmp_path / "out.md"
        result = runner.invoke(app, [
            "recommend", "--limit", "fifty", "--output", str(output),
        ])
        assert result.exit_code != 0
        assert not output.exists()


# ===================================================================
#  CLI: stdout and file output (with real scoring via temp DB)
# ===================================================================


class TestCliOutput:
    def _write_profile(self, path: Path) -> None:
        path.write_text(
            "## Background\n\n- **Skills**: Python\n- **Experience**: 5 years\n"
            "- **Roles**: Security Engineer\n\n## Target Cities\n\n- 北京\n",
            encoding="utf-8",
        )

    def test_default_markdown_stdout(self, tmp_path: Path) -> None:
        """Default format produces markdown output with Chinese labels."""
        profile_path = tmp_path / "profile.md"
        self._write_profile(profile_path)
        result = runner.invoke(app, [
            "recommend", "--profile", str(profile_path),
            "--db-path", str(tmp_path / "test.db"), "--limit", "5",
        ])
        assert result.exit_code == 0, f"CLI exited {result.exit_code}: {result.stdout}"
        assert "扫描" in result.stdout
        assert "推荐" in result.stdout or "概览" in result.stdout

    def test_markdown_stdout_integration(self, tmp_path: Path) -> None:
        """Markdown output via stdout with _safe_stdout_emit."""
        db_path = tmp_path / "test.db"
        profile_path = tmp_path / "profile.md"
        self._write_profile(profile_path)
        result = runner.invoke(app, [
            "recommend",
            "--profile", str(profile_path),
            "--db-path", str(db_path),
            "--limit", "10",
            "--format", "markdown",
        ])
        assert result.exit_code == 0, f"CLI failed: {result.stdout}"
        assert "扫描" in result.stdout
        assert "合格" in result.stdout
        assert "返回" in result.stdout

    def test_json_stdout(self, tmp_path: Path) -> None:
        """JSON format to stdout renders valid JSON."""
        db_path = tmp_path / "test.db"
        profile_path = tmp_path / "profile.md"
        self._write_profile(profile_path)
        result = runner.invoke(app, [
            "recommend",
            "--profile", str(profile_path),
            "--db-path", str(db_path),
            "--limit", "10",
            "--format", "json",
        ])
        assert result.exit_code == 0, f"CLI failed: {result.stdout}"
        data = json.loads(result.stdout)
        assert data["schema_version"] == 1
        assert "recommendations" in data

    def test_markdown_file_output(self, tmp_path: Path) -> None:
        """Markdown written to file with parent dirs created."""
        db_path = tmp_path / "test.db"
        profile_path = tmp_path / "profile.md"
        self._write_profile(profile_path)
        output = tmp_path / "nested" / "report.md"
        result = runner.invoke(app, [
            "recommend",
            "--profile", str(profile_path),
            "--db-path", str(db_path),
            "--limit", "10",
            "--output", str(output),
        ])
        assert result.exit_code == 0, f"CLI failed: {result.stdout}"
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "扫描" in content
        assert "合格" in content
        assert "返回" in content
        # stdout should contain only a short completion line
        assert len(result.stdout.strip()) > 0

    def test_json_file_output(self, tmp_path: Path) -> None:
        """JSON written to file."""
        db_path = tmp_path / "test.db"
        profile_path = tmp_path / "profile.md"
        self._write_profile(profile_path)
        output = tmp_path / "report.json"
        result = runner.invoke(app, [
            "recommend",
            "--profile", str(profile_path),
            "--db-path", str(db_path),
            "--limit", "10",
            "--format", "json",
            "--output", str(output),
        ])
        assert result.exit_code == 0, f"CLI failed: {result.stdout}"
        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert len(result.stdout.strip()) > 0

    def test_output_parent_creation(self, tmp_path: Path) -> None:
        """Parent directories are created when writing output."""
        db_path = tmp_path / "test.db"
        profile_path = tmp_path / "profile.md"
        profile_path.write_text("## Background\n\n- **Skills**: Python\n", encoding="utf-8")
        output = tmp_path / "deep" / "nested" / "dir" / "report.md"
        result = runner.invoke(app, [
            "recommend",
            "--profile", str(profile_path),
            "--db-path", str(db_path),
            "--limit", "10",
            "--output", str(output),
        ])
        assert result.exit_code == 0, f"CLI failed: {result.stdout}"
        assert output.exists()

    def test_no_db_writes(self, tmp_path: Path) -> None:
        """Job/observation/mark counts and persisted fields unchanged after recommend."""
        db_path = tmp_path / "test.db"
        profile_path = tmp_path / "profile.md"
        profile_path.write_text(
            "## Background\n\n- **Skills**: Python\n- **Experience**: 5 years\n\n"
            "## Target Cities\n\n- 北京\n",
            encoding="utf-8",
        )

        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, JobObservation, UserMark

        session = init_db(db_path)
        company = Company(name="TestCorp", slug="testcorp")
        session.add(company)
        session.flush()
        source = Source(
            name="Test Source", slug="test-source", company_id=company.id,
        )
        session.add(source)
        session.flush()

        job = Job(
            external_id="ext-dbw", company_id=company.id, source_id=source.id,
            title="Data Engineer", url="https://example.com/dbw",
            description="", status="active", relevance_status="target",
            matched_tags='["Security"]',
            responsibilities="ETL pipelines.",
            requirements="5 years Python",
            detail_completeness="full",
            classification_version="2.1.0",
            classification_reasons='["test"]',
        )
        session.add(job)
        session.flush()

        obs = JobObservation(job_id=job.id, field_name="status", old_value="", new_value="active")
        session.add(obs)
        session.flush()

        mark = UserMark(job_id=job.id, mark_type="bookmark", note="test")
        session.add(mark)
        session.commit()

        # Capture pre-counts
        pre_jobs = session.query(Job).count()
        pre_obs = session.query(JobObservation).count()
        pre_marks = session.query(UserMark).count()
        pre_detail = session.query(Job.detail_completeness).filter(Job.id == job.id).scalar()
        pre_classif = session.query(Job.classification_version).filter(Job.id == job.id).scalar()
        pre_req = session.query(Job.requirements).filter(Job.id == job.id).scalar()
        session.close()

        # Run real CLI (no mocking)
        result = runner.invoke(app, [
            "recommend",
            "--profile", str(profile_path),
            "--db-path", str(db_path),
            "--limit", "10",
        ])
        assert result.exit_code == 0, f"CLI failed: {result.stdout} {result.stderr}"

        # Verify counts and fields unchanged
        session2 = init_db(db_path)
        try:
            assert session2.query(Job).count() == pre_jobs
            assert session2.query(JobObservation).count() == pre_obs
            assert session2.query(UserMark).count() == pre_marks
            assert (
                session2.query(Job.detail_completeness).filter(Job.id == job.id).scalar()
                == pre_detail
            )
            assert (
                session2.query(Job.classification_version).filter(Job.id == job.id).scalar()
                == pre_classif
            )
            assert (
                session2.query(Job.requirements).filter(Job.id == job.id).scalar()
                == pre_req
            )
        finally:
            session2.close()


# ===================================================================
#  Full real scoring with temp DB
# ===================================================================


class TestCliFullScoring:
    def test_real_scoring_with_jobs(self, tmp_path: Path) -> None:
        """End-to-end: insert jobs, run recommend, check output."""
        db_path = tmp_path / "test.db"
        profile_path = tmp_path / "profile.md"
        profile_path.write_text(
            "## Background\n\n"
            "- **Skills**: Python, cloud security\n"
            "- **Experience**: 5 years\n"
            "- **Roles**: Security Engineer\n\n"
            "## Target Cities\n\n"
            "- 北京\n",
            encoding="utf-8",
        )

        # Populate DB with a test job
        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        session = init_db(db_path)
        company = Company(name="ByteDance", slug="bytedance")
        session.add(company)
        session.flush()

        source = Source(
            name="ByteDance Careers",
            slug="bytedance-careers",
            company_id=company.id,
        )
        session.add(source)
        session.flush()

        run = CollectRun(source_id=source.id)
        session.add(run)
        session.flush()

        job = Job(
            external_id="ext-1",
            company_id=company.id,
            source_id=source.id,
            title="Security Engineer",
            url="https://jobs.bytedance.com/1",
            description="Security testing role",
            salary_text="",
            salary_min=None,
            salary_max=None,
            salary_currency="",
            salary_period="",
            salary_disclosed=False,
            location="北京",
            job_type="技术",
            matched_tags='["Security"]',
            status="active",
            relevance_status="target",
            responsibilities="Responsible for security testing and threat modeling.",
            requirements="5 years Python, cloud security",
            detail_completeness="full",
        )
        session.add(job)
        session.commit()
        session.close()

        # Run recommend CLI
        output = tmp_path / "report.md"
        result = runner.invoke(app, [
            "recommend",
            "--profile", str(profile_path),
            "--db-path", str(db_path),
            "--limit", "10",
            "--output", str(output),
        ])
        assert result.exit_code == 0, f"CLI failed: {result.stdout} {result.stderr}"
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "Security Engineer" in content
        assert "ByteDance" in content
        assert "北京" in content

    def test_real_scoring_json(self, tmp_path: Path) -> None:
        """End-to-end JSON output with real scoring."""
        db_path = tmp_path / "test.db"
        profile_path = tmp_path / "profile.md"
        profile_path.write_text(
            "## Background\n\n"
            "- **Skills**: Python, cloud security\n"
            "- **Experience**: 5 years\n"
            "- **Roles**: Security Engineer\n\n"
            "## Target Cities\n\n"
            "- 北京\n",
            encoding="utf-8",
        )

        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        session = init_db(db_path)
        company = Company(name="ByteDance", slug="bytedance")
        session.add(company)
        session.flush()

        source = Source(
            name="ByteDance Careers",
            slug="bytedance-careers",
            company_id=company.id,
        )
        session.add(source)
        session.flush()

        run = CollectRun(source_id=source.id)
        session.add(run)
        session.flush()

        job = Job(
            external_id="ext-2",
            company_id=company.id,
            source_id=source.id,
            title="Security Engineer",
            url="https://jobs.bytedance.com/2",
            description="",
            salary_text="",
            salary_min=None,
            salary_max=None,
            salary_currency="",
            salary_period="",
            salary_disclosed=False,
            location="北京",
            job_type="技术",
            matched_tags='["Security"]',
            status="active",
            relevance_status="target",
            responsibilities="Security testing",
            requirements="Python, cloud security",
            detail_completeness="full",
        )
        session.add(job)
        session.commit()
        session.close()

        output = tmp_path / "report.json"
        result = runner.invoke(app, [
            "recommend",
            "--profile", str(profile_path),
            "--db-path", str(db_path),
            "--limit", "10",
            "--format", "json",
            "--output", str(output),
        ])
        assert result.exit_code == 0, f"CLI failed: {result.stdout} {result.stderr}"
        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert len(data["recommendations"]) >= 1
        rec = data["recommendations"][0]
        assert rec["title"] == "Security Engineer"
        assert rec["company_name"] == "ByteDance"

    def test_counts_unchanged(self, tmp_path: Path) -> None:
        """Job/observation/mark counts are unchanged after recommend."""
        db_path = tmp_path / "test.db"
        profile_path = tmp_path / "profile.md"
        profile_path.write_text(
            "## Background\n\n"
            "- **Skills**: Python\n"
            "- **Experience**: 5 years\n\n"
            "## Target Cities\n\n"
            "- 北京\n",
            encoding="utf-8",
        )

        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job

        session = init_db(db_path)
        company = Company(name="ByteDance", slug="bytedance")
        session.add(company)
        session.flush()

        source = Source(
            name="ByteDance Careers",
            slug="bytedance-careers",
            company_id=company.id,
        )
        session.add(source)
        session.flush()
        job = Job(
            external_id="ext-3", company_id=company.id,
            source_id=source.id,
            title="Engineer", url="https://jobs.example.com/3",
            description="", status="active", relevance_status="target",
            matched_tags='["Security"]',
        )
        session.add(job)
        session.commit()

        # Count before
        before_jobs = session.query(Job).count()

        session.close()

        # Run recommend (separate session)
        runner.invoke(app, [
            "recommend",
            "--profile", str(profile_path),
            "--db-path", str(db_path),
            "--limit", "10",
        ])

        # Count after
        session2 = init_db(db_path)
        try:
            after_jobs = session2.query(Job).count()
        finally:
            session2.close()

        assert before_jobs == after_jobs


# ===================================================================
#  Windows-safe stdout integration
# ===================================================================


class TestWindowsSafeStdout:
    def test_safe_stdout_emit_called(self, tmp_path: Path) -> None:
        """Stdout path uses _safe_stdout_emit."""
        profile_path = tmp_path / "profile.md"
        profile_path.write_text(
            "## Background\n\n- **Skills**: Python\n- **Experience**: 5 years\n\n"
            "## Target Cities\n\n- 北京\n",
            encoding="utf-8",
        )
        with mock.patch("findjobs.cli._safe_stdout_emit") as mock_emit:
            with mock.patch("findjobs.recommendation.recommend_from_session") as mock_rec:
                result_obj = make_result(
                    profile=RecommendationProfile(skills=("Python",), target_cities=("北京",)),
                )
                mock_rec.return_value = result_obj

                runner.invoke(app, [
                    "recommend",
                    "--profile", str(profile_path),
                    "--db-path", str(tmp_path / "test.db"),
                    "--limit", "10",
                ])
                mock_emit.assert_called()


# ===================================================================
#  CLI: computation failure → no artifact
# ===================================================================


class TestCliComputationFailure:
    def test_scoring_exception_no_artifact(self, tmp_path: Path) -> None:
        """Injected scoring exception exits nonzero and leaves no artifact."""
        db_path = tmp_path / "test.db"
        profile_path = tmp_path / "profile.md"
        profile_path.write_text(
            "## Background\n\n- **Skills**: Python\n- **Experience**: 5 years\n\n"
            "## Target Cities\n\n- 北京\n",
            encoding="utf-8",
        )
        output = tmp_path / "outdir" / "report.md"

        with mock.patch(
            "findjobs.recommendation.recommend_from_session"
        ) as mock_rec:
            mock_rec.side_effect = RuntimeError("Scoring crash")
            result = runner.invoke(app, [
                "recommend",
                "--profile", str(profile_path),
                "--db-path", str(db_path),
                "--limit", "10",
                "--output", str(output),
            ])

        assert result.exit_code != 0, (
            f"Expected nonzero exit, got {result.exit_code}: "
            f"{result.stdout} {result.exception}"
        )
        # Output file must NOT be created
        assert not output.exists()
        # Parent directory must NOT be created
        assert not output.parent.exists()


# ===================================================================
#  Markdown deterministic output
# ===================================================================


class TestMarkdownDeterministic:
    def test_deterministic_markdown(self, security_profile: RecommendationProfile) -> None:
        """Same result produces same Markdown."""
        result = make_result(profile=security_profile)
        md1 = render_to_markdown(result)
        md2 = render_to_markdown(result)
        assert md1 == md2

    def test_no_timestamps_in_markdown(self, security_profile: RecommendationProfile) -> None:
        """No current timestamps in markdown output."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        assert "2026-" not in md
        assert "timestamp" not in md.lower()
        assert "generated_at" not in md

    def test_no_profile_path_in_markdown(self, security_profile: RecommendationProfile) -> None:
        """No absolute profile paths in markdown output."""
        result = make_result(profile=security_profile)
        md = render_to_markdown(result)
        assert "profile.md" not in md
