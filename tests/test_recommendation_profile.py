"""Tests for deterministic recommendation profile loading."""

from dataclasses import fields
from pathlib import Path

import pytest
from pydantic import ValidationError

from findjobs.profile_import import (
    Profile,
    render_markdown,
)
from findjobs.recommendation_profile import (
    RecommendationProfile,
    load_recommendation_profile,
)


# ====================  Immutable contract  ===================================


class TestRecommendationProfileImmutable:
    """RecommendationProfile is frozen, has only allowed fields, no PII."""

    ALLOWED = {
        "skills",
        "experience_years",
        "roles",
        "target_cities",
        "target_roles",
        "excluded_companies",
        "work_types",
        "constraints",
    }

    def test_frozen(self):
        rp = RecommendationProfile()
        with pytest.raises(AttributeError):
            rp.skills = ["Python"]

    def test_only_recommendation_fields(self):
        """No PII, raw text, source metadata, or contact fields exist."""
        field_names = {f.name for f in fields(RecommendationProfile)}
        assert field_names == self.ALLOWED

    def test_no_pii_fields(self):
        """Explicitly ensure PII-bearing fields are absent."""
        forbidden = {
            "source_sha256", "source_kind", "contact_redacted",
            "experiences", "projects", "education",
            "name", "phone", "email", "address",
            "raw_text", "filename", "resume",
        }
        field_names = {f.name for f in fields(RecommendationProfile)}
        assert field_names.isdisjoint(forbidden)

    def test_nested_state_is_immutable(self):
        """Tuples ensure nested state cannot be mutated."""
        rp = RecommendationProfile(skills=("Python", "Rust"))
        with pytest.raises(AttributeError):
            rp.skills.append("Go")  # type: ignore[attr-defined]

    def test_defaults_are_empty(self):
        rp = RecommendationProfile()
        assert rp.skills == ()
        assert rp.experience_years is None
        assert rp.roles == ()
        assert rp.target_cities == ()
        assert rp.target_roles == ()
        assert rp.excluded_companies == ()
        assert rp.work_types == ()
        assert rp.constraints == ()


# ====================  JSON loading  =========================================


class TestJsonLoading:
    def test_json_projection(self, tmp_path):
        """JSON validates through Profile schema, projects only recommendation fields."""
        full = Profile(
            source_kind="docx",
            source_sha256="a" * 64,
            contact_redacted=True,
            skills=["Python", "Rust"],
            experience_years=5.0,
            roles=["Security Engineer"],
            target_cities=["北京", "上海"],
            target_roles=["Security Researcher"],
            excluded_companies=["huawei"],
            work_types=["remote", "hybrid"],
            constraints=["Must have good work-life balance"],
        )
        path = tmp_path / "profile.json"
        path.write_text(full.model_dump_json(indent=2), encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.skills == ("Python", "Rust")
        assert rp.experience_years == 5.0
        assert rp.roles == ("Security Engineer",)
        assert rp.target_cities == ("北京", "上海")
        assert rp.target_roles == ("Security Researcher",)
        assert rp.excluded_companies == ("huawei",)
        assert rp.work_types == ("remote", "hybrid")
        assert rp.constraints == ("Must have good work-life balance",)

    def test_empty_fields_in_json(self, tmp_path):
        """Empty optional fields produce empty tuples / None."""
        profile = Profile(
            source_kind="docx",
            source_sha256="b" * 64,
        )
        path = tmp_path / "profile.json"
        path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.skills == ()
        assert rp.experience_years is None
        assert rp.roles == ()
        assert rp.target_cities == ()
        assert rp.target_roles == ()
        assert rp.excluded_companies == ()
        assert rp.work_types == ()
        assert rp.constraints == ()

    def test_deduplicates_json(self, tmp_path):
        """Deduplicates list fields case-insensitively, preserving first spelling."""
        profile = Profile(
            source_kind="docx",
            source_sha256="c" * 64,
            skills=["Python", "python", "PYTHON", "Rust"],
        )
        path = tmp_path / "profile.json"
        path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.skills == ("Python", "Rust")

    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{invalid json}", encoding="utf-8")
        with pytest.raises(ValueError, match="Malformed JSON"):
            load_recommendation_profile(path)

    def test_invalid_schema_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text('{"source_kind": "unknown"}', encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid profile schema"):
            load_recommendation_profile(path)

    def test_json_alias_canonicalization(self, tmp_path):
        """JSON target cities are canonicalized via location helpers."""
        profile = Profile(
            source_kind="docx",
            source_sha256="g" * 64,
            target_cities=["Beijing", "shanghai", "南山"],
        )
        path = tmp_path / "profile.json"
        path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert "北京" in rp.target_cities
        assert "上海" in rp.target_cities
        assert "深圳" in rp.target_cities

    def test_json_remote_canonicalization(self, tmp_path):
        """JSON work_types canoncialize Remote/远程 to remote."""
        profile = Profile(
            source_kind="docx",
            source_sha256="h" * 64,
            target_cities=["Remote"],
            work_types=["远程", "Remote", "Hybrid"],
        )
        path = tmp_path / "profile.json"
        path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert "remote" in rp.target_cities
        assert rp.work_types == ("remote", "Hybrid")

    def test_json_multi_city_alias(self, tmp_path):
        """JSON single field with comma-separated cities is canonicalized."""
        profile = Profile(
            source_kind="docx",
            source_sha256="i" * 64,
            target_cities=["Beijing, Shanghai"],
        )
        path = tmp_path / "profile.json"
        path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert "北京" in rp.target_cities
        assert "上海" in rp.target_cities


# ====================  Markdown loading  =====================================


class TestMarkdownLoading:
    def test_basic_parsing(self, tmp_path):
        """Full markdown is parsed into all recommendation fields."""
        md = """\
## Background

_Contact information has been redacted from this profile._

- **Total experience**: 5.0 years
- **Roles**: Security Engineer, Penetration Tester
- **Skills**: Python, Rust

## Target Cities

- 北京
- 上海

## Target Roles

- Security Researcher
- AppSec Engineer

## Salary Expectation

_Not specified. Salary expectation must not be estimated._

## Preferences

- Remote
- Hybrid

## Excluded Companies

- huawei

## Constraints

- Must have great work-life balance
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.skills == ("Python", "Rust")
        assert rp.experience_years == 5.0
        assert rp.roles == ("Security Engineer", "Penetration Tester")
        assert rp.target_cities == ("北京", "上海")
        assert rp.target_roles == ("Security Researcher", "AppSec Engineer")
        assert rp.excluded_companies == ("huawei",)
        assert rp.work_types == ("remote", "Hybrid")
        assert rp.constraints == ("Must have great work-life balance",)

    def test_no_background_labels(self, tmp_path):
        """Absent labeled values default safely."""
        md = """\
## Background

_Contact information has been redacted from this profile._

## Target Roles

- Engineer
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.skills == ()
        assert rp.experience_years is None
        assert rp.roles == ()

    def test_experience_years_none_when_missing(self, tmp_path):
        md = """\
## Background

_Contact information has been redacted from this profile._

- **Skills**: Python
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.experience_years is None
        assert rp.skills == ("Python",)

    def test_experience_years_label_alternative(self, tmp_path):
        """'Experience' label also works."""
        md = """\
## Background

- **Experience**: 8 years
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.experience_years == 8.0

    def test_roles_label_singular(self, tmp_path):
        """'Role' label works as singular."""
        md = """\
## Background

- **Role**: Security Engineer
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.roles == ("Security Engineer",)

    def test_not_specified_ignored(self, tmp_path):
        """'_Not specified._' lines are not parsed as values."""
        md = """\
## Target Cities

_Not specified._

## Target Roles

_Not specified._

## Preferences

_Not specified._

## Constraints

_Not specified._
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.target_cities == ()
        assert rp.target_roles == ()
        assert rp.work_types == ()
        assert rp.constraints == ()

    def test_empty_bullets_ignored(self, tmp_path):
        """Lines that are just '- ' (empty bullets) are skipped."""
        md = """\
## Target Cities

- 北京
-
- 上海
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.target_cities == ("北京", "上海")

    def test_horizontal_rules_ignored(self, tmp_path):
        md = """\
## Target Cities

- 北京
---
- 上海
___
- 深圳
***
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.target_cities == ("北京", "上海", "深圳")

    def test_case_insensitive_sections(self, tmp_path):
        """## section headings are matched case-insensitively."""
        md = """\
## BACKGROUND

_Contact information has been redacted from this profile._

- **Skills**: Python

## target cities

- Beijing

## TARGET ROLES

- Engineer

## preferences

- Remote

## EXCLUDED COMPANIES

- huawei

## CONSTRAINTS

- Must have WLB
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.skills == ("Python",)
        assert rp.target_cities == ("北京",)
        assert rp.target_roles == ("Engineer",)
        assert rp.work_types == ("remote",)
        assert rp.excluded_companies == ("huawei",)
        assert rp.constraints == ("Must have WLB",)

    def test_chinese_separators(self, tmp_path):
        """Roles/Skills split on ASCII comma, Chinese comma, and 、."""
        md = """\
## Background

- **Roles**: Security Engineer， Penetration Tester
- **Skills**: Python、Rust、Go
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.roles == ("Security Engineer", "Penetration Tester")
        assert rp.skills == ("Python", "Rust", "Go")

    def test_experience_years_punctuation_only(self, tmp_path):
        """Punctuation-only or non-numeric labeled values stay None, no crash."""
        md = """\
## Background

- **Experience**: N/A
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.experience_years is None

    def test_labeled_preferences(self, tmp_path):
        """Preferences support both bare bullets and labeled Job Type."""
        md = """\
## Preferences

- **Job Type**: Full-time
- Remote
- **Schedule**: Flexible
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert "Full-time" in rp.work_types
        assert "remote" in rp.work_types
        assert "Flexible" in rp.work_types

    def test_constraints_label_not_stripped(self, tmp_path):
        """Constraints keep labeled markup intact (labels carry meaning)."""
        md = """\
## Constraints

- **Must have**: Great WLB
- No overtime
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert "**Must have**: Great WLB" in rp.constraints
        assert "No overtime" in rp.constraints


# ====================  Section isolation  ====================================


class TestSectionIsolation:
    def test_cities_in_work_experience_not_leaked(self, tmp_path):
        """City names under Work Experience are not parsed as target cities."""
        md = """\
## Target Cities

- 北京

## Work Experience

- **FooCorp** — Engineer (2020 – 2024)
  - Worked in Shanghai on various projects
  - Also spent time in Shenzhen
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.target_cities == ("北京",)

    def test_companies_in_projects_not_leaked(self, tmp_path):
        """Companies mentioned in Projects are not added to excluded_companies."""
        md = """\
## Excluded Companies

- huawei

## Projects

- **Internal Tool** (Side Project)
  - Built for Alibaba
  - Also used at Tencent
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.excluded_companies == ("huawei",)


# ====================  City alias & remote canonicalization  =================


class TestCanonicalization:
    def test_city_alias_canonicalized(self, tmp_path):
        """'Beijing' is canonicalized to '北京'."""
        md = """\
## Target Cities

- Beijing
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.target_cities == ("北京",)

    def test_multi_city_bullets(self, tmp_path):
        """Multiple cities per bullet are handled."""
        md = """\
## Target Cities

- Beijing, Shanghai
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        # Each bullet line is parsed as one item, then canonicalized individually
        cities = set(rp.target_cities)
        assert "北京" in cities
        assert "上海" in cities

    def test_remote_canonicalized(self, tmp_path):
        """Remote/远程 in cities is canonicalized to 'remote'."""
        md = """\
## Target Cities

- Remote
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.target_cities == ("remote",)

    def test_remote_chinese_canonicalized(self, tmp_path):
        md = """\
## Target Cities

- 远程
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.target_cities == ("remote",)

    def test_remote_in_work_types(self, tmp_path):
        """Remote/远程 in Preferences is canonicalized to 'remote'."""
        md = """\
## Preferences

- Remote
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.work_types == ("remote",)

    def test_remote_chinese_work_types(self, tmp_path):
        md = """\
## Preferences

- 远程
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.work_types == ("remote",)

    def test_diverse_aliases(self, tmp_path):
        """Various Shenzhen aliases all canonicalize to '深圳'."""
        md = """\
## Target Cities

- shenzhen
- 深圳市
- 南山
- 南山区
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.target_cities == ("深圳",)


# ====================  Stable deduplication  =================================


class TestStableDeduplication:
    def test_case_insensitive_dedup(self, tmp_path):
        """Same city in different cases deduplicates, preserving first form."""
        md = """\
## Target Cities

- Beijing
- beijing
- BEIJING
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.target_cities == ("北京",)

    def test_skills_dedup_stable(self, tmp_path):
        """Skills deduplicate case-insensitively, first spelling preserved."""
        md = """\
## Background

- **Skills**: Python, python, PYTHON, Rust
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.skills == ("Python", "Rust")

    def test_roles_dedup_stable(self, tmp_path):
        md = """\
## Background

- **Roles**: Security Engineer, security engineer, SECURITY ENGINEER
"""
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.roles == ("Security Engineer",)


# ====================  Missing / invalid inputs  =============================


class TestErrorHandling:
    def test_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError, match="not found"):
            load_recommendation_profile(missing)

    def test_missing_markdown_file(self, tmp_path):
        missing = tmp_path / "nonexistent.md"
        with pytest.raises(FileNotFoundError, match="not found"):
            load_recommendation_profile(missing)

    def test_unsupported_extension(self, tmp_path):
        bad = tmp_path / "profile.txt"
        bad.write_text("hello", encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported file extension"):
            load_recommendation_profile(bad)

    def test_unsupported_extension_pdf(self, tmp_path):
        bad = tmp_path / "profile.docx"
        bad.write_text("hello", encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported file extension"):
            load_recommendation_profile(bad)


# ====================  Round-trip  ===========================================


class TestMarkdownRoundTrip:
    def test_round_trip_full(self, tmp_path):
        """Full profile round-trips through markdown."""
        profile = Profile(
            source_kind="docx",
            source_sha256="d" * 64,
            skills=["Python", "Rust", "Go"],
            experience_years=5.0,
            roles=["Security Engineer"],
            target_cities=["北京", "上海"],
            target_roles=["Security Researcher", "AppSec Engineer"],
            excluded_companies=["huawei"],
            work_types=["remote", "hybrid"],
            constraints=["Must have good work-life balance"],
        )
        md = render_markdown(profile)
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.skills == ("Python", "Rust", "Go")
        assert rp.experience_years == 5.0
        assert rp.roles == ("Security Engineer",)
        assert rp.target_cities == ("北京", "上海")
        assert rp.target_roles == ("Security Researcher", "AppSec Engineer")
        assert rp.excluded_companies == ("huawei",)
        assert rp.work_types == ("remote", "hybrid")
        assert rp.constraints == ("Must have good work-life balance",)

    def test_round_trip_empty(self, tmp_path):
        """Empty optional fields round-trip as empty tuples / None."""
        profile = Profile(
            source_kind="docx",
            source_sha256="e" * 64,
        )
        md = render_markdown(profile)
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.skills == ()
        assert rp.experience_years is None
        assert rp.roles == ()
        assert rp.target_cities == ()
        assert rp.target_roles == ()
        assert rp.excluded_companies == ()
        assert rp.work_types == ()
        assert rp.constraints == ()

    def test_round_trip_partial(self, tmp_path):
        """Partial fields (no experience_years, only some lists) round-trip."""
        profile = Profile(
            source_kind="pdf",
            source_sha256="f" * 64,
            skills=["Python"],
            target_cities=["深圳"],
            work_types=["remote"],
        )
        md = render_markdown(profile)
        path = tmp_path / "profile.md"
        path.write_text(md, encoding="utf-8")

        rp = load_recommendation_profile(path)
        assert rp.skills == ("Python",)
        assert rp.experience_years is None
        assert rp.target_cities == ("深圳",)
        assert rp.work_types == ("remote",)
        assert rp.target_roles == ()
        assert rp.excluded_companies == ()
        assert rp.constraints == ()
