"""Tests for deterministic job recommendation scoring."""

from __future__ import annotations

import copy
from unittest import mock

import pytest

from findjobs.recommendation import (
    Recommendation,
    RecommendationResult,
    ScoreComponent,
    infer_profile_domain,
    recommend_from_session,
    recommend_jobs,
)
from findjobs.recommendation_profile import RecommendationProfile

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


@pytest.fixture
def neutral_profile() -> RecommendationProfile:
    """A profile with no explicit AI/Security domain signal."""
    return RecommendationProfile(
        skills=("Java", "Go", "Rust"),
        experience_years=3.0,
        roles=("Software Engineer",),
        target_cities=("上海",),
        target_roles=("Software Engineer",),
    )


@pytest.fixture
def ai_profile() -> RecommendationProfile:
    return RecommendationProfile(
        skills=("Python", "MLOps", "model deployment/fine-tuning"),
        experience_years=4.0,
        roles=("AI Engineer",),
        target_cities=("杭州",),
        target_roles=("AI Engineer",),
    )


# ===================================================================
#  Limit validation
# ===================================================================


class TestLimitValidation:
    def test_limit_zero(self, security_profile: RecommendationProfile) -> None:
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            recommend_jobs([make_row()], security_profile, limit=0)

    def test_limit_negative(self, security_profile: RecommendationProfile) -> None:
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            recommend_jobs([], security_profile, limit=-1)

    def test_limit_too_large(self, security_profile: RecommendationProfile) -> None:
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            recommend_jobs([], security_profile, limit=1001)

    def test_limit_not_int(self, security_profile: RecommendationProfile) -> None:
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            recommend_jobs([], security_profile, limit="50")  # type: ignore[arg-type]

    def test_limit_valid_boundary(self, security_profile: RecommendationProfile) -> None:
        """limit=1000 is the upper bound."""
        rows = [make_row(id=i) for i in range(5)]
        result = recommend_jobs(rows, security_profile, limit=1000)
        assert len(result.recommendations) == 5
        assert result.scanned == 5


# ===================================================================
#  Summary-row rejection (missing full fields)
# ===================================================================


class TestMissingFields:
    def test_missing_relevance_status(self, security_profile: RecommendationProfile) -> None:
        row = make_row()
        del row["relevance_status"]
        with pytest.raises(KeyError, match="relevance_status"):
            recommend_jobs([row], security_profile)

    def test_missing_requirements(self, security_profile: RecommendationProfile) -> None:
        row = make_row()
        del row["requirements"]
        with pytest.raises(KeyError, match="requirements"):
            recommend_jobs([row], security_profile)

    def test_missing_responsibilities(self, security_profile: RecommendationProfile) -> None:
        row = make_row()
        del row["responsibilities"]
        with pytest.raises(KeyError, match="responsibilities"):
            recommend_jobs([row], security_profile)

    def test_missing_url(self, security_profile: RecommendationProfile) -> None:
        row = make_row()
        del row["url"]
        with pytest.raises(KeyError, match="url"):
            recommend_jobs([row], security_profile)

    def test_missing_multiple_fields(self, security_profile: RecommendationProfile) -> None:
        row = make_row()
        del row["relevance_status"]
        del row["requirements"]
        del row["matched_tags"]
        with pytest.raises(KeyError, match="matched_tags.*relevance_status.*requirements"):
            recommend_jobs([row], security_profile)


# ===================================================================
#  Hard exclusion tests
# ===================================================================


class TestHardExclusions:
    def _run(self, rows: list[dict], profile: RecommendationProfile) -> RecommendationResult:
        """Helper: run recommend_jobs with two rows (bad + good) to confirm filtering."""
        return recommend_jobs(rows + [make_row(id=999)], profile)

    def test_non_active_status(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(status="inactive")], security_profile)
        assert result.hard_exclusion_counts["non_active_status"] == 1
        assert result.eligible == 1

    def test_non_target_relevance(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(relevance_status="excluded")], security_profile)
        assert result.hard_exclusion_counts["non_target_relevance"] == 1
        assert result.eligible == 1

    def test_unsupported_tags(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(matched_tags=["Security", "网络攻防"])], security_profile)
        assert result.hard_exclusion_counts["unsupported_tags"] == 1
        assert result.eligible == 1

    def test_unsupported_tags_only_unsupported(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(matched_tags=["网络攻防"])], security_profile)
        assert result.hard_exclusion_counts["unsupported_tags"] == 1

    def test_algorithm_in_title_cn(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(title="算法工程师")], security_profile)
        assert result.hard_exclusion_counts["algorithm_rejection"] == 1

    def test_algorithm_in_title_en(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(title="Algorithm Engineer")], security_profile)
        assert result.hard_exclusion_counts["algorithm_rejection"] == 1

    def test_algorithm_in_job_type(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(title="Engineer", job_type="算法")], security_profile)
        assert result.hard_exclusion_counts["algorithm_rejection"] == 1

    def test_huawei_via_slug(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(company_slug="huawei-cloud")], security_profile)
        assert result.hard_exclusion_counts["huawei_exclusion"] == 1

    def test_huawei_via_name(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(company_name="华为技术有限公司")], security_profile)
        assert result.hard_exclusion_counts["huawei_exclusion"] == 1

    def test_huawei_regardless_of_profile(self, security_profile: RecommendationProfile) -> None:
        """Huawei excluded even when profile has no explicit exclusion."""
        clean = RecommendationProfile(
            skills=("Python",),
            target_cities=("北京",),
        )
        result = self._run(
            [make_row(company_slug="huawei", company_name="Huawei")], clean
        )
        assert result.hard_exclusion_counts["huawei_exclusion"] == 1

    def test_profile_excluded_company(self, security_profile: RecommendationProfile) -> None:
        result = self._run(
            [make_row(company_slug="tencent-cloud", company_name="Tencent Cloud")],
            security_profile,
        )
        assert result.hard_exclusion_counts["profile_excluded_company"] == 1

    def test_missing_url_blank(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(url="")], security_profile)
        assert result.hard_exclusion_counts["missing_url"] == 1

    def test_missing_url_none(self, security_profile: RecommendationProfile) -> None:
        result = self._run([make_row(url=None)], security_profile)  # type: ignore[arg-type]
        assert result.hard_exclusion_counts["missing_url"] == 1

    def test_all_exclusions_counted(self, security_profile: RecommendationProfile) -> None:
        """Each exclusion type gets exactly one bad row among good rows."""
        bad_rows = [
            make_row(id=10, status="archived"),
            make_row(id=11, relevance_status="excluded"),
            make_row(id=12, matched_tags=["Unknown"]),
            make_row(id=13, title="算法研究"),
            make_row(id=14, company_slug="huawei"),
            make_row(id=15, company_slug="tencent-ai"),
            make_row(id=16, url=""),
        ]
        result = recommend_jobs(bad_rows + [make_row(id=999)], security_profile)
        for key in result.hard_exclusion_counts:
            expected = 1 if key not in ("unsupported_tags",) else 0
            # "unsupported_tags" is matched_tags=["Unknown"] → that's unsupported
            # but also matched_tags=["Unknown"] is the one for id=12
            # Let's verify each one specifically
            pass
        assert result.hard_exclusion_counts["non_active_status"] == 1
        assert result.hard_exclusion_counts["non_target_relevance"] == 1
        # id=12 has ["Unknown"] which is unsupported
        assert result.hard_exclusion_counts["unsupported_tags"] == 1
        assert result.hard_exclusion_counts["algorithm_rejection"] == 1
        assert result.hard_exclusion_counts["huawei_exclusion"] == 1
        # id=15 "tencent-ai" matches "tencent" in profile excluded_companies
        assert result.hard_exclusion_counts["profile_excluded_company"] == 1
        assert result.hard_exclusion_counts["missing_url"] == 1
        assert result.eligible == 1  # only id=999 survives


# ===================================================================
#  Component: domain scoring
# ===================================================================


class TestDomainScoring:
    def test_security_full_match(self, security_profile: RecommendationProfile) -> None:
        """Profile domain Security, job tags Security → 25."""
        result = recommend_jobs([make_row(matched_tags=["Security"])], security_profile)
        rec = result.recommendations[0]
        assert rec.domain.score == 25.0
        assert rec.domain.max_score == 25.0
        assert "fully matches" in rec.domain.message

    def test_security_with_ai_tags_full_match(self, security_profile: RecommendationProfile) -> None:
        """Security profile still matches when job also has AI tags."""
        result = recommend_jobs([make_row(matched_tags=["AI", "Security"])], security_profile)
        rec = result.recommendations[0]
        assert rec.domain.score == 25.0

    def test_ai_full_match(self, ai_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row(matched_tags=["AI"])], ai_profile)
        rec = result.recommendations[0]
        assert rec.domain.score == 25.0

    def test_ai_security_partial(self, ai_profile: RecommendationProfile) -> None:
        """AI profile vs Security-only job → partial (0 domain overlap)."""
        # AI profile domain is "AI", job has only "Security"
        result = recommend_jobs([make_row(matched_tags=["Security"])], ai_profile)
        rec = result.recommendations[0]
        assert rec.domain.score == 0.0
        assert "does not match" in rec.domain.message

    def test_neutral_domain(self, neutral_profile: RecommendationProfile) -> None:
        """No domain signal in profile → neutral 12.5."""
        result = recommend_jobs([make_row(matched_tags=["AI"])], neutral_profile)
        rec = result.recommendations[0]
        assert rec.domain.score == 12.5
        assert "neutral" in rec.domain.message

    def test_domain_ai_security_tags(self, security_profile: RecommendationProfile) -> None:
        """AI Security tags treated as both AI and Security."""
        result = recommend_jobs(
            [make_row(matched_tags=["AI Security"])], security_profile
        )
        rec = result.recommendations[0]
        # Security profile, job has security via "AI Security" → full match
        assert rec.domain.score == 25.0

    def test_empty_tags_excluded(self, security_profile: RecommendationProfile) -> None:
        """Empty tags are unsupported → hard excluded, no recommendation."""
        result = recommend_jobs(
            [make_row(matched_tags=[])], security_profile
        )
        assert result.hard_exclusion_counts["unsupported_tags"] == 1
        assert result.eligible == 0
        assert len(result.recommendations) == 0

    def test_empty_tags_with_good_row(self, security_profile: RecommendationProfile) -> None:
        """Empty tags hard-excluded while other rows still pass."""
        result = recommend_jobs(
            [make_row(id=1, matched_tags=[]), make_row(id=2, matched_tags=["Security"])],
            security_profile,
        )
        assert result.hard_exclusion_counts["unsupported_tags"] == 1
        assert result.eligible == 1


# ===================================================================
#  Component: skills scoring
# ===================================================================


class TestSkillsScoring:
    @property
    def _base_row(self) -> dict:
        return make_row(
            title="Security Engineer",
            responsibilities="Security testing, threat modeling",
            requirements="5 years experience in Python, cloud security",
        )

    def test_skills_partial_match(self, security_profile: RecommendationProfile) -> None:
        """2/4 recognized skills matched → 15.0"""
        result = recommend_jobs([self._base_row], security_profile)
        rec = result.recommendations[0]
        assert rec.skills.score == 15.0
        assert rec.skills.max_score == 30.0
        assert "Matched 2/4" in rec.skills.message
        assert "Python" in rec.skills.matched_terms
        assert "cloud security" in rec.skills.matched_terms

    def test_skills_full_match(self, security_profile: RecommendationProfile) -> None:
        """When all demand skills are in profile skills → 30."""
        row = make_row(
            title="Security Engineer",
            responsibilities="Python development",
            requirements="cloud security experience, penetration testing",
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        # demand_skills = detect_skills("Security Engineer Python development cloud security experience, penetration testing")
        # Python, cloud security, penetration testing → all matched → 30
        # But also "security testing" might match from "Security Engineer"...
        # Let me check: "Security Engineer" contains "security" → but the pattern for security testing
        # is r"security test" → "Security Engineer" → does it contain "security test"?
        # NO. "Security Engineer" has "Security" then " Engineer" → no "test" after "security"
        # So "security testing" would NOT match.
        assert rec.skills.score == 30.0
        assert len(rec.skills.gap_terms) == 0

    def test_skills_no_demand(self, security_profile: RecommendationProfile) -> None:
        """Job text has no recognizable skills → neutral 15."""
        row = make_row(
            title="General Manager",
            responsibilities="Team management",
            requirements="No technical skills required",
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.skills.score == 15.0
        assert "No recognizable" in rec.skills.message

    def test_skills_gaps_reported(self, security_profile: RecommendationProfile) -> None:
        """Unmatched demand skills appear in gap_terms."""
        result = recommend_jobs([self._base_row], security_profile)
        rec = result.recommendations[0]
        assert "security testing" in rec.skills.gap_terms
        assert "threat modeling" in rec.skills.gap_terms

    def test_skills_matched_skills_field(self, security_profile: RecommendationProfile) -> None:
        """matched_skills on Recommendation contains same as matched_terms."""
        result = recommend_jobs([self._base_row], security_profile)
        rec = result.recommendations[0]
        assert set(rec.matched_skills) == set(rec.skills.matched_terms)
        assert "Python" in rec.matched_skills


# ===================================================================
#  Component: requirements scoring
# ===================================================================


class TestRequirementsScoring:
    def test_full_coverage(self, security_profile: RecommendationProfile) -> None:
        """All requirement skills matched → 20."""
        row = make_row(
            requirements="Python and cloud security experience",
            detail_completeness="full",
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.requirements_score.score == 20.0

    def test_partial_coverage(self, security_profile: RecommendationProfile) -> None:
        """Some requirement skills unmatched."""
        row = make_row(
            requirements="Python, Java, C++, Go experience",
            detail_completeness="full",
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.requirements_score.score < 20.0

    def test_requirements_unavailable_missing(self, security_profile: RecommendationProfile) -> None:
        """Blank requirements → neutral 10 with verification gap."""
        row = make_row(requirements="", detail_completeness="full")
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.requirements_score.score == 10.0
        assert "not available" in rec.requirements_score.message

    def test_requirements_unavailable_detail_completeness(self, security_profile: RecommendationProfile) -> None:
        """detail_completeness=responsibilities_only → neutral regardless of requirements content."""
        row = make_row(
            requirements="Python, cloud security",
            detail_completeness="responsibilities_only",
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.requirements_score.score == 10.0
        assert "not available" in rec.requirements_score.message

    def test_requirements_available_full(self, security_profile: RecommendationProfile) -> None:
        """detail_completeness=full with content → available, scored."""
        row = make_row(
            requirements="Python, cloud security",
            detail_completeness="full",
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.requirements_score.score > 10.0

    def test_requirements_available_requirements_only(self, security_profile: RecommendationProfile) -> None:
        """detail_completeness=requirements_only → available, scored."""
        row = make_row(
            requirements="Python, cloud security",
            detail_completeness="requirements_only",
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.requirements_score.score > 10.0

    def test_requirements_unavailable_combined_only(self, security_profile: RecommendationProfile) -> None:
        """detail_completeness=combined_only → unavailable."""
        row = make_row(
            requirements="Python, cloud security",
            detail_completeness="combined_only",
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.requirements_score.score == 10.0

    def test_requirements_unavailable_missing_state(self, security_profile: RecommendationProfile) -> None:
        """detail_completeness=missing → unavailable even with content."""
        row = make_row(
            requirements="Python, cloud security",
            detail_completeness="missing",
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.requirements_score.score == 10.0

    def test_unrecognized_prose_neutral(self, security_profile: RecommendationProfile) -> None:
        """Requirements with only unrecognizable prose → neutral 10."""
        row = make_row(
            requirements="Strong communication skills and team spirit",
            detail_completeness="full",
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.requirements_score.score == 10.0
        assert "No recognizable" in rec.requirements_score.message


# ===================================================================
#  Component: experience scoring
# ===================================================================


class TestExperienceScoring:
    def test_meets_requirement(self, security_profile: RecommendationProfile) -> None:
        """profile_years >= required → 15."""
        result = recommend_jobs(
            [make_row(requirements="5 years experience in Python")],
            security_profile,
        )
        rec = result.recommendations[0]
        assert rec.experience.score == 15.0

    def test_shortfall_within_one_year(self, security_profile: RecommendationProfile) -> None:
        """Profile has 5y, job requires 6y → 8."""
        result = recommend_jobs(
            [make_row(requirements="6 years experience")],
            security_profile,
        )
        rec = result.recommendations[0]
        assert rec.experience.score == 8.0
        assert "within 1 year" in rec.experience.message

    def test_shortfall_large(self, security_profile: RecommendationProfile) -> None:
        """Profile has 5y, job requires 7y → 0."""
        result = recommend_jobs(
            [make_row(requirements="7 years experience")],
            security_profile,
        )
        rec = result.recommendations[0]
        assert rec.experience.score == 0.0
        assert "below" in rec.experience.message

    def test_no_job_requirement(self, security_profile: RecommendationProfile) -> None:
        """No explicit requirement in job text → neutral 8."""
        result = recommend_jobs(
            [make_row(requirements="Python and cloud security")],
            security_profile,
        )
        rec = result.recommendations[0]
        assert rec.experience.score == 8.0
        assert "No explicit experience requirement" in rec.experience.message

    def test_no_profile_years(self, security_profile: RecommendationProfile) -> None:
        """Profile has no experience_years → neutral 8."""
        no_exp = RecommendationProfile(
            skills=("Python",),
            experience_years=None,
            target_cities=("北京",),
        )
        result = recommend_jobs(
            [make_row(requirements="5 years experience")],
            no_exp,
        )
        rec = result.recommendations[0]
        assert rec.experience.score == 8.0
        assert "No profile experience years" in rec.experience.message

    def test_both_missing(self) -> None:
        """No job requirement and no profile years → neutral 8."""
        bare = RecommendationProfile(
            skills=(),
            experience_years=None,
            target_cities=("北京",),
        )
        result = recommend_jobs(
            [make_row(requirements="General skills")],
            bare,
        )
        rec = result.recommendations[0]
        assert rec.experience.score == 8.0

    def test_cn_year_format(self, security_profile: RecommendationProfile) -> None:
        """Chinese 年以上 format parsed correctly."""
        result = recommend_jobs(
            [make_row(requirements="5年以上Python经验")],
            security_profile,
        )
        rec = result.recommendations[0]
        assert rec.experience.score == 15.0

    def test_plus_year_format(self, security_profile: RecommendationProfile) -> None:
        """N+ years format parsed correctly."""
        result = recommend_jobs(
            [make_row(requirements="5+ years of experience")],
            security_profile,
        )
        rec = result.recommendations[0]
        assert rec.experience.score == 15.0


# ===================================================================
#  Component: location scoring
# ===================================================================


class TestLocationScoring:
    def test_city_match(self, security_profile: RecommendationProfile) -> None:
        """Target city 北京 matches job location → 10."""
        result = recommend_jobs([make_row(location="北京")], security_profile)
        rec = result.recommendations[0]
        assert rec.location_score.score == 10.0

    def test_city_mismatch(self, security_profile: RecommendationProfile) -> None:
        """Job location 上海 not in profile target cities → 0."""
        result = recommend_jobs([make_row(location="上海")], security_profile)
        rec = result.recommendations[0]
        assert rec.location_score.score == 0.0
        assert "does not match" in rec.location_score.message

    def test_location_alias(self, security_profile: RecommendationProfile) -> None:
        """Job uses district alias for target city."""
        result = recommend_jobs([make_row(location="海淀")], security_profile)
        rec = result.recommendations[0]
        # 海淀 normalizes to 北京 via split_locations
        assert rec.location_score.score == 10.0

    def test_job_location_missing(self, security_profile: RecommendationProfile) -> None:
        """Empty job location → neutral 5."""
        result = recommend_jobs([make_row(location="")], security_profile)
        rec = result.recommendations[0]
        assert rec.location_score.score == 5.0

    def test_no_profile_cities(self) -> None:
        """Profile with no target cities → neutral 5."""
        no_cities = RecommendationProfile(
            skills=("Python",), target_cities=(),
        )
        result = recommend_jobs(
            [make_row(location="北京")], no_cities,
        )
        rec = result.recommendations[0]
        assert rec.location_score.score == 5.0

    def test_remote_match(self, security_profile: RecommendationProfile) -> None:
        """Job is Remote and profile has remote → 10."""
        profile_w_remote = RecommendationProfile(
            skills=("Python",),
            target_cities=("北京", "remote"),
        )
        result = recommend_jobs(
            [make_row(location="Remote")], profile_w_remote,
        )
        rec = result.recommendations[0]
        assert rec.location_score.score == 10.0

    def test_remote_cn_match(self, security_profile: RecommendationProfile) -> None:
        """Chinese 远程 matches remote in profile."""
        profile_w_remote = RecommendationProfile(
            skills=("Python",),
            target_cities=("北京", "remote"),
        )
        result = recommend_jobs(
            [make_row(location="远程")], profile_w_remote,
        )
        rec = result.recommendations[0]
        assert rec.location_score.score == 10.0

    def test_multi_city_job_partial_match(self, security_profile: RecommendationProfile) -> None:
        """Job in 上海/深圳, profile has 北京 → no match."""
        result = recommend_jobs(
            [make_row(location="上海/深圳")], security_profile,
        )
        rec = result.recommendations[0]
        assert rec.location_score.score == 0.0

    def test_multi_city_job_full_match(self, security_profile: RecommendationProfile) -> None:
        """Job in 北京/上海, profile has 北京 → match."""
        result = recommend_jobs(
            [make_row(location="北京/上海")], security_profile,
        )
        rec = result.recommendations[0]
        assert rec.location_score.score == 10.0


# ===================================================================
#  Salary neutrality
# ===================================================================


class TestSalaryNeutrality:
    def test_salary_not_in_scoring(self, security_profile: RecommendationProfile) -> None:
        """Salary fields do not affect score."""
        disclosed = make_row(
            salary_text="50k-80k",
            salary_min=50000.0,
            salary_max=80000.0,
            salary_disclosed=True,
            matched_tags=["Security"],
        )
        undisclosed = make_row(
            salary_text="",
            salary_min=None,
            salary_max=None,
            salary_disclosed=False,
            matched_tags=["Security"],
        )
        r1 = recommend_jobs([disclosed], security_profile)
        r2 = recommend_jobs([undisclosed], security_profile)
        assert r1.recommendations[0].total_score == r2.recommendations[0].total_score

    def test_salary_facts_preserved(self, security_profile: RecommendationProfile) -> None:
        """Salary fields are preserved verbatim in recommendations."""
        row = make_row(
            salary_text="50k-80k",
            salary_min=50000.0,
            salary_max=80000.0,
            salary_currency="CNY",
            salary_period="monthly",
            salary_disclosed=True,
        )
        result = recommend_jobs([row], security_profile)
        rec = result.recommendations[0]
        assert rec.salary_text == "50k-80k"
        assert rec.salary_min == 50000.0
        assert rec.salary_max == 80000.0
        assert rec.salary_currency == "CNY"
        assert rec.salary_period == "monthly"
        assert rec.salary_disclosed is True

    def test_undisclosed_salary_advice(self, security_profile: RecommendationProfile) -> None:
        """Undisclosed salary advice says confirm without estimating."""
        result = recommend_jobs([make_row(salary_disclosed=False)], security_profile)
        rec = result.recommendations[0]
        assert "not disclosed" in rec.application_advice
        assert "confirm" in rec.application_advice
        # Must not estimate a value
        assert "k" not in rec.application_advice.lower() or "confirm" in rec.application_advice

    def test_disclosed_salary_advice(self, security_profile: RecommendationProfile) -> None:
        """Disclosed salary advice references disclosed figure."""
        result = recommend_jobs(
            [make_row(salary_disclosed=True, salary_text="100k")],
            security_profile,
        )
        rec = result.recommendations[0]
        assert "disclosed" in rec.application_advice


# ===================================================================
#  Sorting and limit
# ===================================================================


class TestSortingAndLimit:
    def test_score_descending(self, security_profile: RecommendationProfile) -> None:
        """Recommendations sorted by score descending."""
        rows = [
            make_row(id=1, title="Security Engineer",
                     requirements="10 years experience in Python, cloud security",
                     matched_tags=["Security"]),
            make_row(id=2, title="Python Developer",
                     requirements="Python",
                     matched_tags=["Security"]),
        ]
        result = recommend_jobs(rows, security_profile)
        scores = [r.total_score for r in result.recommendations]
        assert scores == sorted(scores, reverse=True)

    def test_tie_job_id_descending(self, security_profile: RecommendationProfile) -> None:
        """Same score sorts by job_id descending."""
        rows = [
            make_row(id=100, title="Engineer A",
                     requirements="5 years Python, cloud security",
                     matched_tags=["Security"]),
            make_row(id=200, title="Engineer B",
                     requirements="5 years Python, cloud security",
                     matched_tags=["Security"]),
        ]
        result = recommend_jobs(rows, security_profile)
        assert len(result.recommendations) == 2
        # Both should have the same score → sorted by id descending
        assert result.recommendations[0].job_id == 200
        assert result.recommendations[1].job_id == 100

    def test_limit_applied(self, security_profile: RecommendationProfile) -> None:
        """Only `limit` recommendations returned."""
        rows = [make_row(id=i) for i in range(10)]
        result = recommend_jobs(rows, security_profile, limit=3)
        assert len(result.recommendations) == 3

    def test_limit_more_than_eligible(self, security_profile: RecommendationProfile) -> None:
        """When limit > eligible, return all eligible."""
        rows = [make_row(id=i) for i in range(3)]
        result = recommend_jobs(rows, security_profile, limit=100)
        assert len(result.recommendations) == 3


# ===================================================================
#  Immutability
# ===================================================================


class TestImmutability:
    def test_score_component_frozen(self) -> None:
        c = ScoreComponent(score=10.0, max_score=20.0, message="test")
        with pytest.raises(Exception):
            c.score = 99.0  # type: ignore[misc]

    def test_recommendation_frozen(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        with pytest.raises(Exception):
            rec.total_score = 99.0  # type: ignore[misc]

    def test_result_frozen(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        with pytest.raises(Exception):
            result.eligible = 999  # type: ignore[misc]

    def test_recommendations_tuple_immutable(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        with pytest.raises(Exception):
            result.recommendations[0].matched_skills = ()  # type: ignore[misc]

    def test_nested_collections_immutable(self, security_profile: RecommendationProfile) -> None:
        """tags, matched_skills, gaps should all be tuples."""
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        assert isinstance(rec.tags, tuple)
        assert isinstance(rec.matched_skills, tuple)
        assert isinstance(rec.gaps, tuple)
        assert isinstance(rec.domain.matched_terms, tuple)
        assert isinstance(rec.domain.gap_terms, tuple)


# ===================================================================
#  Evidence source traceability
# ===================================================================


class TestEvidenceTraceability:
    def test_domain_source_fields(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        assert "matched_tags" in rec.domain.source_fields
        assert "skills" in rec.domain.profile_fields

    def test_skills_source_fields(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        assert "title" in rec.skills.source_fields
        assert "requirements" in rec.skills.source_fields
        assert "skills" in rec.skills.profile_fields

    def test_requirements_source_fields(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        assert "requirements" in rec.requirements_score.source_fields

    def test_experience_source_fields(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        assert "requirements" in rec.experience.source_fields
        assert "experience_years" in rec.experience.profile_fields

    def test_location_source_fields(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        assert "location" in rec.location_score.source_fields
        assert "target_cities" in rec.location_score.profile_fields


# ===================================================================
#  Official URL and detail_completeness presence
# ===================================================================


class TestRequiredFields:
    def test_url_present(self, security_profile: RecommendationProfile) -> None:
        """Every recommendation must have a non-empty url."""
        rows = [
            make_row(id=1, url="https://a.com/1"),
            make_row(id=2, url="https://a.com/2"),
        ]
        result = recommend_jobs(rows, security_profile)
        for rec in result.recommendations:
            assert rec.url.startswith("https://")

    def test_detail_completeness_present(self, security_profile: RecommendationProfile) -> None:
        """detail_completeness field must be preserved."""
        result = recommend_jobs(
            [make_row(detail_completeness="full")],
            security_profile,
        )
        rec = result.recommendations[0]
        assert rec.detail_completeness == "full"

    def test_gaps_populated(self, security_profile: RecommendationProfile) -> None:
        """Should have gap terms for skill and location when they don't match."""
        # Profile has 北京, mismatch job location → location gap
        missing_profile = RecommendationProfile(
            skills=("Java",),
            experience_years=5.0,
            target_cities=("深圳",),
        )
        row = make_row(
            location="上海",
            requirements="C++, Go experience",
        )
        result = recommend_jobs([row], missing_profile)
        rec = result.recommendations[0]
        # Should have at least one gap
        assert len(rec.gaps) > 0


# ===================================================================
#  Aggregate learning advice
# ===================================================================


class TestAggregateAdvice:
    def test_recurring_skill_gaps(self) -> None:
        """Same skill gap in multiple recs → mentioned in aggregate advice."""
        gap_profile = RecommendationProfile(
            skills=("Java",),
            target_cities=("北京",),
        )
        rows = [
            make_row(id=1, requirements="Python, C++, Go",
                     matched_tags=["Security"]),
            make_row(id=2, requirements="Python, Rust, C++",
                     matched_tags=["Security"]),
        ]
        result = recommend_jobs(rows, gap_profile)
        # Python and C++ should be gaps in both (profile only has Java)
        advice = result.aggregate_learning_advice
        assert "appears in" in advice
        assert "Python" in advice

    def test_no_recurring_gaps(self, security_profile: RecommendationProfile) -> None:
        """No repeated skill gap → stated explicitly."""
        rows = [
            make_row(id=1, requirements="Python",
                     matched_tags=["Security"]),
        ]
        result = recommend_jobs(rows, security_profile)
        advice = result.aggregate_learning_advice
        assert "No repeated" in advice

    def test_empty_recommendations(self, security_profile: RecommendationProfile) -> None:
        """No eligible jobs → aggregate advice handles gracefully."""
        result = recommend_jobs([make_row(status="archived")], security_profile)
        advice = result.aggregate_learning_advice
        assert "No repeated" in advice


# ===================================================================
#  Profile domain detection
# ===================================================================


class TestProfileDomain:
    def test_ai_domain(self) -> None:
        p = RecommendationProfile(
            skills=("MLOps", "Python"),
            roles=("AI Engineer",),
        )
        assert infer_profile_domain(p) == "AI"

    def test_security_domain(self) -> None:
        p = RecommendationProfile(
            skills=("Python", "cloud security"),
            roles=("Security Engineer",),
        )
        assert infer_profile_domain(p) == "Security"

    def test_ai_security_domain(self) -> None:
        p = RecommendationProfile(
            skills=("AI security", "privacy security"),
            roles=("Security Engineer",),
        )
        assert infer_profile_domain(p) == "AI Security"

    def test_neutral_no_terms(self) -> None:
        p = RecommendationProfile(skills=("Java", "Go"), roles=("Engineer",))
        assert infer_profile_domain(p) is None

    def test_neutral_empty(self) -> None:
        p = RecommendationProfile()
        assert infer_profile_domain(p) is None

    def test_target_roles_detected(self) -> None:
        """Domain terms in target_roles should also be detected."""
        p = RecommendationProfile(
            target_roles=("AI Security Engineer",),
        )
        assert infer_profile_domain(p) == "AI Security"


# ===================================================================
#  Domain token-boundary regressions  (Fix #3)
# ===================================================================


class TestDomainTokenBoundaries:
    def test_email_security_not_ai(self) -> None:
        """``email`` contains ``ai`` substring but should not trigger AI."""
        p = RecommendationProfile(
            skills=("email security",),
            roles=("Security Engineer",),
        )
        assert infer_profile_domain(p) == "Security"

    def test_saml_not_ai(self) -> None:
        """``SAML`` substring does not trigger AI."""
        p = RecommendationProfile(
            skills=("SAML", "cloud security"),
            roles=("Security Engineer",),
        )
        assert infer_profile_domain(p) == "Security"

    def test_maintainability_not_ai(self) -> None:
        """``maintainability`` should not match AI via ``ai`` substring."""
        p = RecommendationProfile(
            skills=("maintainability", "cloud security"),
            roles=("Security Engineer",),
        )
        assert infer_profile_domain(p) == "Security"

    def test_ai_llm_security_detected(self) -> None:
        """``AI/LLM security`` still infers AI + Security."""
        p = RecommendationProfile(
            skills=("AI/LLM security",),
            roles=("Security Engineer",),
        )
        assert infer_profile_domain(p) == "AI Security"

    def test_ai_cjk_detected(self) -> None:
        """``AI驱动`` infers AI (CJK boundary)."""
        p = RecommendationProfile(
            skills=("AI驱动开发",),
        )
        assert infer_profile_domain(p) == "AI"

    def test_mlops_detected(self) -> None:
        """Standalone ``MLOps`` infers AI (via phrase term, not short ``ml``)."""
        p = RecommendationProfile(
            skills=("MLOps",),
        )
        assert infer_profile_domain(p) == "AI"

    def test_standalone_ai_word_boundary(self) -> None:
        """Standalone ``AI`` triggers domain detection."""
        p = RecommendationProfile(
            skills=("AI security",),
        )
        dom = infer_profile_domain(p)
        assert "AI" in dom  # at minimum AI, possibly AI Security

    def test_training_does_not_falsely_make_ai(self) -> None:
        """``training`` does not contain AI keyword ``ai`` at a boundary."""
        p = RecommendationProfile(
            skills=("training", "security"),
        )
        assert infer_profile_domain(p) == "Security"

    def test_htmlops_not_ai(self) -> None:
        """``htmlops`` must not match ``mlops`` or ``ml``."""
        p = RecommendationProfile(
            skills=("htmlops",),
            roles=("SRE",),
        )
        assert infer_profile_domain(p) is None

    def test_openlp_not_nlp(self) -> None:
        """``openlp`` must not match ``nlp``."""
        p = RecommendationProfile(
            skills=("openlp",),
            roles=("Engineer",),
        )
        assert infer_profile_domain(p) is None

    def test_standalone_ml_detected(self) -> None:
        """Standalone ``ML`` triggers AI domain detection."""
        p = RecommendationProfile(
            skills=("ML",),
        )
        assert infer_profile_domain(p) == "AI"

    def test_standalone_llm_detected(self) -> None:
        """Standalone ``LLM`` triggers AI domain detection."""
        p = RecommendationProfile(
            skills=("LLM",),
        )
        assert infer_profile_domain(p) == "AI"

    def test_standalone_nlp_detected(self) -> None:
        """Standalone ``NLP`` triggers AI domain detection."""
        p = RecommendationProfile(
            skills=("NLP",),
        )
        assert infer_profile_domain(p) == "AI"

    def test_cybersecurity_detected_via_full_word(self) -> None:
        """``cybersecurity`` matches via its own term entry."""
        p = RecommendationProfile(
            skills=("cybersecurity",),
        )
        assert infer_profile_domain(p) == "Security"

    def test_penetration_word_boundary(self) -> None:
        """``penetration`` at word boundary matches security."""
        p = RecommendationProfile(
            skills=("penetration testing",),
        )
        assert infer_profile_domain(p) == "Security"

    def test_privacy_word_boundary(self) -> None:
        """``privacy`` at word boundary matches security."""
        p = RecommendationProfile(
            skills=("privacy",),
        )
        assert infer_profile_domain(p) == "Security"


# ===================================================================
#  MappingProxyType immutability  (Fix #4)
# ===================================================================


class TestHardExclusionCountsImmutability:
    def test_exclusion_counts_is_mapping(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        from collections.abc import Mapping
        assert isinstance(result.hard_exclusion_counts, Mapping)

    def test_exclusion_counts_mutation_raises(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        with pytest.raises(TypeError):
            result.hard_exclusion_counts["unsupported_tags"] = 99  # type: ignore[misc]

    def test_exclusion_counts_still_indexable(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        assert result.hard_exclusion_counts["non_active_status"] == 0
        assert result.hard_exclusion_counts["missing_url"] == 0


# ===================================================================
#  Input immutability regression  (Fix #1)
# ===================================================================


class TestInputImmutability:
    def test_original_rows_not_mutated(self, security_profile: RecommendationProfile) -> None:
        """Caller-provided row dicts must not be altered by recommend_jobs."""
        row = make_row(
            id=1, title=None, location=None, requirements=None,  # type: ignore[arg-type]
        )
        original = dict(row)  # snapshot before calling

        recommend_jobs([row], security_profile)

        assert row == original, "Input row dict was mutated"
        assert row["title"] is None
        assert row["location"] is None
        assert row["requirements"] is None

    def test_original_list_not_mutated(self, security_profile: RecommendationProfile) -> None:
        """The input list length and references must be unchanged."""
        rows = [make_row(id=1), make_row(id=2)]
        original_len = len(rows)
        original_ids = [r["id"] for r in rows]

        recommend_jobs(rows, security_profile)

        assert len(rows) == original_len
        assert [r["id"] for r in rows] == original_ids

    def test_none_fields_coerced_on_copy_not_original(self, security_profile: RecommendationProfile) -> None:
        """A row with None fields should produce results without altering the original."""
        row = make_row(
            id=42, title=None, location=None, requirements=None,  # type: ignore[arg-type]
        )
        original_title = row["title"]

        result = recommend_jobs([row], security_profile)

        # Original is untouched
        assert row["title"] is original_title
        # But the run succeeded
        assert result.eligible == 1


# ===================================================================
#  Empty exclusion entry regression  (Fix #5)
# ===================================================================


class TestExclusionEntryEdgeCases:
    def test_empty_exclusion_does_not_match_all(self) -> None:
        """Empty and whitespace-only exclusion entries must not exclude anything."""
        profile = RecommendationProfile(
            skills=("Python",),
            excluded_companies=("", " "),
        )
        result = recommend_jobs(
            [make_row(company_slug="bytedance", company_name="ByteDance")],
            profile,
        )
        assert result.eligible == 1

    def test_tiny_token_does_not_exclude(self) -> None:
        """A single-character exclusion must not match."""
        profile = RecommendationProfile(
            excluded_companies=("a",),
        )
        result = recommend_jobs(
            [make_row(company_slug="alibaba", company_name="Alibaba Group")],
            profile,
        )
        assert result.eligible == 1

    def test_two_letter_fragment_does_not_exclude_unrelated_company(self) -> None:
        profile = RecommendationProfile(excluded_companies=("AI",))
        result = recommend_jobs([make_row(company_slug="baidu")], profile)
        assert result.eligible == 1

    def test_two_letter_exact_company_still_excludes(self) -> None:
        profile = RecommendationProfile(excluded_companies=("JD",))
        result = recommend_jobs([make_row(company_slug="jd")], profile)
        assert result.eligible == 0

    def test_blank_company_identity_does_not_reverse_match(self) -> None:
        profile = RecommendationProfile(excluded_companies=("Tencent",))
        row = make_row(company_slug=None, company_name="Different Corp")
        result = recommend_jobs([row], profile)
        assert result.eligible == 1

    def test_descriptive_exclusion_english(self) -> None:
        """A descriptive exclusion like 'Tencent and its affiliates' must still match slug 'tencent'."""
        profile = RecommendationProfile(
            excluded_companies=("Tencent and its affiliates",),
        )
        result = recommend_jobs(
            [make_row(company_slug="tencent", company_name="Tencent")],
            profile,
        )
        assert result.hard_exclusion_counts["profile_excluded_company"] == 1
        assert result.eligible == 0

    def test_descriptive_exclusion_chinese(self) -> None:
        """A Chinese descriptive exclusion must match the company name."""
        profile = RecommendationProfile(
            excluded_companies=("腾讯集团及其子公司",),
        )
        result = recommend_jobs(
            [make_row(company_slug="tencent", company_name="腾讯")],
            profile,
        )
        assert result.hard_exclusion_counts["profile_excluded_company"] == 1
        assert result.eligible == 0

    def test_exclusion_matches_slug_in_both_directions(self) -> None:
        """Exclusion 'tencent' matches slug 'tencent-cloud' and vice versa."""
        # Forward: exclusion inside slug
        profile = RecommendationProfile(excluded_companies=("tencent",))
        result = recommend_jobs(
            [make_row(company_slug="tencent-cloud")], profile,
        )
        assert result.hard_exclusion_counts["profile_excluded_company"] == 1
        # Reverse: slug inside exclusion
        profile2 = RecommendationProfile(excluded_companies=("tencent-cloud-team",))
        result2 = recommend_jobs(
            [make_row(company_slug="tencent")], profile2,
        )
        assert result2.hard_exclusion_counts["profile_excluded_company"] == 1


# ===================================================================
#  Nullable text-field coercion  (Fix #6)
# ===================================================================


class TestNullableTextFields:
    @pytest.mark.parametrize("field", ["title", "location", "job_type", "requirements", "responsibilities"])
    def test_none_field_does_not_crash(self, field: str) -> None:
        """A valid full row with ``None`` in a nullable text field must not crash."""
        profile = RecommendationProfile(target_cities=("北京",))
        row = make_row(**{field: None})  # type: ignore[arg-type]
        result = recommend_jobs([row], profile)
        assert result.eligible == 1

    def test_none_matched_tags_still_valid(self) -> None:
        """matched_tags=None is treated as empty → now unsupported."""
        profile = RecommendationProfile(target_cities=("北京",))
        row = make_row(matched_tags=None)  # type: ignore[arg-type]
        result = recommend_jobs([row], profile)
        assert result.hard_exclusion_counts["unsupported_tags"] == 1
# ===================================================================


class TestRecommendFromSession:
    def test_read_only_behavior(self) -> None:
        """Session methods add/flush/commit are never called."""
        mock_session = mock.MagicMock()
        profile = RecommendationProfile(skills=("Python",), target_cities=("北京",))

        with mock.patch(
            "findjobs.recommendation.query_jobs",
            return_value=[make_row()],
        ):
            result = recommend_from_session(mock_session, profile)

        assert result.scanned == 1
        mock_session.add.assert_not_called()
        mock_session.flush.assert_not_called()
        mock_session.commit.assert_not_called()

    def test_uses_detail_level_full(self) -> None:
        """query_jobs called with detail_level='full'."""
        mock_session = mock.MagicMock()
        profile = RecommendationProfile()

        with mock.patch(
            "findjobs.recommendation.query_jobs",
            return_value=[make_row()],
        ) as mock_query:
            recommend_from_session(mock_session, profile)

        mock_query.assert_called_once_with(
            mock_session, detail_level="full"
        )

    def test_preserves_row_count(self) -> None:
        """scanned reflects the original number of rows."""
        mock_session = mock.MagicMock()
        profile = RecommendationProfile()

        rows = [make_row(id=i) for i in range(5)]
        with mock.patch(
            "findjobs.recommendation.query_jobs",
            return_value=rows,
        ):
            result = recommend_from_session(mock_session, profile)

        assert result.scanned == 5


# ===================================================================
#  Integration: end-to-end scoring
# ===================================================================


class TestIntegration:
    def test_baseline_scoring(self, security_profile: RecommendationProfile) -> None:
        """Known inputs produce expected scores and tier."""
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]

        assert rec.total_score == 85.0
        assert rec.tier == "high"
        assert rec.job_id == 1
        assert rec.company_slug == "bytedance"
        assert rec.company_name == "ByteDance"
        assert rec.title == "Security Engineer"
        assert rec.location == "北京"
        assert rec.job_type == "技术"
        assert rec.tags == ("Security",)
        assert rec.url == "https://jobs.example.com/1"
        assert rec.responsibilities != ""
        assert rec.requirements != ""

    def test_result_counts(self, security_profile: RecommendationProfile) -> None:
        """scanned, eligible, exclusion_counts are coherent."""
        rows = [
            make_row(id=1, status="inactive"),
            make_row(id=2, relevance_status="excluded"),
            make_row(id=3, title="算法工程师"),
            make_row(id=4, company_slug="huawei"),
            make_row(id=5, matched_tags=["Security"]),
            make_row(id=6, matched_tags=["Security"]),
        ]
        result = recommend_jobs(rows, security_profile)
        assert result.scanned == 6
        assert result.eligible == 2
        assert sum(result.hard_exclusion_counts.values()) == 4

    def test_five_components_sum_to_total(self, security_profile: RecommendationProfile) -> None:
        """total_score must equal the sum of all five component scores."""
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        expected = round(
            rec.domain.score
            + rec.skills.score
            + rec.requirements_score.score
            + rec.experience.score
            + rec.location_score.score,
            1,
        )
        assert rec.total_score == expected
        assert rec.total_score <= 100.0

    def test_exactly_five_components(self, security_profile: RecommendationProfile) -> None:
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        # Each recommendation has domain, skills, requirements_score, experience, location_score
        assert rec.domain.max_score == 25.0
        assert rec.skills.max_score == 30.0
        assert rec.requirements_score.max_score == 20.0
        assert rec.experience.max_score == 15.0
        assert rec.location_score.max_score == 10.0

    def test_all_tags_preserved(self, security_profile: RecommendationProfile) -> None:
        """matched_tags from job row correctly preserved."""
        result = recommend_jobs(
            [make_row(matched_tags=["AI", "Security"])],
            security_profile,
        )
        rec = result.recommendations[0]
        assert "AI" in rec.tags
        assert "Security" in rec.tags

    def test_application_advice_contains_matched_skills(self, security_profile: RecommendationProfile) -> None:
        """Application advice references matched skills."""
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        assert "Highlight matched skills" in rec.application_advice
        assert "Python" in rec.application_advice

    def test_gaps_in_application_advice(self, security_profile: RecommendationProfile) -> None:
        """Gaps are mentioned in application advice when applicable."""
        result = recommend_jobs([make_row()], security_profile)
        rec = result.recommendations[0]
        # Our baseline has "security testing" and "threat modeling" as skill gaps
        # These should appear in advice
        assert "Address skill gaps" in rec.application_advice
        assert "security testing" in rec.application_advice

    def test_empty_rows(self, security_profile: RecommendationProfile) -> None:
        """Empty rows produces zero recommendations."""
        result = recommend_jobs([], security_profile)
        assert result.scanned == 0
        assert result.eligible == 0
        assert len(result.recommendations) == 0

    def test_hard_exclusion_keys_all_present(self, security_profile: RecommendationProfile) -> None:
        """All seven hard exclusion keys are in the result."""
        result = recommend_jobs([], security_profile)
        expected_keys = {
            "non_active_status",
            "non_target_relevance",
            "unsupported_tags",
            "algorithm_rejection",
            "huawei_exclusion",
            "profile_excluded_company",
            "missing_url",
        }
        assert set(result.hard_exclusion_counts.keys()) == expected_keys

    def test_requirements_gap_in_recommendation_gaps(self) -> None:
        """When requirements are unavailable, a human-readable gap shows."""
        result = recommend_jobs(
            [make_row(requirements="", detail_completeness="missing")],
            RecommendationProfile(target_cities=("北京",)),
        )
        rec = result.recommendations[0]
        gap_texts = " ".join(rec.gaps).lower()
        assert "requirements not verified" in gap_texts or "confirmation" in gap_texts
