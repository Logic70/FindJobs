from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from findjobs.keyword_analysis import KeywordRules
from findjobs.market_analysis import (
    MarketAnalysisError,
    analyze_market,
    load_market_taxonomy,
    render_market_markdown,
)
from findjobs.recommendation_profile import RecommendationProfile


TAXONOMY_PATH = Path(__file__).parents[1] / "config" / "market_taxonomy.yaml"


def make_row(**overrides: object) -> dict:
    row = {
        "id": 1,
        "company_slug": "example",
        "company_name": "Example",
        "title": "AI安全工程师",
        "location": "北京",
        "job_type": "安全",
        "status": "active",
        "salary_text": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_period": "",
        "salary_disclosed": False,
        "matched_tags": ["AI Security"],
        "url": "https://example.test/jobs/1",
        "first_seen_at": "2026-07-10T00:00:00",
        "last_seen_at": "2026-07-12T00:00:00",
        "published_at": "2026-07-09T00:00:00",
        "relevance_status": "target",
        "classification_version": "2.1.1",
        "classification_reasons": ["ai_and_security_surface_signals"],
        "description": "",
        "responsibilities": "建设大模型安全平台，使用 Docker 和 Kubernetes。",
        "requirements": "必须熟悉 Python 和 AppSec；有 RAG 项目经验者优先。",
        "detail_completeness": "full",
    }
    row.update(overrides)
    return row


@pytest.fixture(scope="module")
def taxonomy():
    return load_market_taxonomy(TAXONOMY_PATH)


def skill(result: dict, term_id: str) -> dict:
    return next(item for item in result["skills"] if item["id"] == term_id)


def domain_signal(result: dict, term_id: str) -> dict:
    return next(item for item in result["domain_signals"] if item["id"] == term_id)


def group(result: dict, dimension: str, key: str) -> dict:
    return next(item for item in result["groups"][dimension] if item["key"] == key)


class TestTaxonomy:
    def test_loads_versioned_taxonomy(self, taxonomy) -> None:
        assert taxonomy.schema_version == 2
        assert taxonomy.taxonomy_version == "2026.07.2"
        assert taxonomy.skills_by_id["python"].name == "Python"
        assert taxonomy.domain_signals_by_id["llm_domain"].name == "大模型领域提及"
        assert "llm" not in taxonomy.skills_by_id

    def test_rejects_previous_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "taxonomy.yaml"
        path.write_text(
            "schema_version: 1\ntaxonomy_version: old",
            encoding="utf-8",
        )

        with pytest.raises(MarketAnalysisError, match="unsupported schema_version"):
            load_market_taxonomy(path)

    def test_duplicate_term_id_fails_with_context(self, tmp_path: Path) -> None:
        path = tmp_path / "taxonomy.yaml"
        path.write_text(
            """
schema_version: 2
taxonomy_version: test
role_families: []
domain_signals: []
skills:
  - {id: python, name: Python, category: language, aliases: [Python]}
  - {id: python, name: Other, category: language, aliases: [Other]}
traits: []
""".strip(),
            encoding="utf-8",
        )

        with pytest.raises(MarketAnalysisError, match="duplicate.*python"):
            load_market_taxonomy(path)

    def test_invalid_alias_fails_fast(self, tmp_path: Path) -> None:
        path = tmp_path / "taxonomy.yaml"
        path.write_text(
            """
schema_version: 2
taxonomy_version: test
role_families: []
domain_signals: []
skills:
  - {id: python, name: Python, category: language, aliases: []}
traits: []
""".strip(),
            encoding="utf-8",
        )

        with pytest.raises(MarketAnalysisError, match="aliases"):
            load_market_taxonomy(path)


class TestSampleContract:
    def test_filters_non_active_non_target_algorithm_and_duplicates(
        self, taxonomy
    ) -> None:
        rows = [
            make_row(id=1),
            make_row(id=1, requirements="重复记录不应进入统计"),
            make_row(id=2, status="archived"),
            make_row(id=3, relevance_status="review"),
            make_row(id=4, title="安全算法工程师"),
            make_row(id=5, job_type="Algorithm"),
        ]

        result = analyze_market(rows, taxonomy, as_of=date(2026, 7, 14))

        assert result["sample"]["input_jobs"] == 6
        assert result["sample"]["analyzed_jobs"] == 1
        assert result["sample"]["excluded"]["duplicate"] == 1
        assert result["sample"]["excluded"]["inactive"] == 1
        assert result["sample"]["excluded"]["non_target"] == 1
        assert result["sample"]["excluded"]["algorithm"] == 2

    def test_missing_requirements_is_unknown_not_negative(self, taxonomy) -> None:
        rows = [
            make_row(id=1, requirements="必须掌握 Python"),
            make_row(
                id=2,
                requirements="",
                detail_completeness="responsibilities_only",
            ),
        ]

        result = analyze_market(rows, taxonomy, as_of=date(2026, 7, 14))
        python = skill(result, "python")

        assert result["quality"]["requirements_available_jobs"] == 1
        assert result["quality"]["requirements_unknown_jobs"] == 1
        assert python["job_count"] == 1
        assert python["job_denominator"] == 1
        assert python["job_coverage"] == 1.0

    def test_responsibility_skill_does_not_become_requirement(self, taxonomy) -> None:
        result = analyze_market(
            [
                make_row(
                    responsibilities="使用 Kubernetes 建设安全平台",
                    requirements="要求具备 Python 开发能力",
                )
            ],
            taxonomy,
            as_of=date(2026, 7, 14),
        )

        kubernetes = skill(result, "kubernetes")
        assert kubernetes["job_count"] == 0
        assert kubernetes["work_content_job_count"] == 1


class TestRequirementSignals:
    def test_broad_llm_mention_is_domain_signal_not_skill(self, taxonomy) -> None:
        profile = RecommendationProfile(skills=("大模型",))
        result = analyze_market(
            [
                make_row(
                    requirements="具备大模型相关经验",
                    responsibilities="参与 LLM 相关业务",
                )
            ],
            taxonomy,
            profile=profile,
            as_of=date(2026, 7, 14),
        )

        assert domain_signal(result, "llm_domain")["job_count"] == 1
        assert "llm_domain" not in [item["id"] for item in result["skills"]]
        assert skill(result, "llm_application")["job_count"] == 0
        assert "llm_domain" not in result["personal_advice"]["covered_skill_ids"]
        assert "llm_domain" not in {
            item["skill_id"]
            for item in result["personal_advice"]["learning_priorities"]
        }
        assert "llm_domain" not in {
            item["skill_id"] for item in result["personal_advice"]["resume_evidence"]
        }

    def test_concrete_llm_capabilities_remain_actionable_skills(self, taxonomy) -> None:
        rows = [
            make_row(id=1, requirements="具备大模型应用开发和工程落地经验"),
            make_row(id=2, requirements="有 LLM 应用架构经验"),
            make_row(id=3, requirements="具备大模型训练经验"),
            make_row(id=4, requirements="负责大模型平台建设"),
            make_row(id=5, requirements="参与 AI 平台研发"),
        ]

        result = analyze_market(rows, taxonomy, as_of=date(2026, 7, 14))

        assert skill(result, "llm_application")["job_count"] == 2
        assert skill(result, "model_training")["job_count"] == 1
        assert skill(result, "ai_platform_engineering")["job_count"] == 2
        assert domain_signal(result, "llm_domain")["job_count"] == 4

    def test_discovered_candidates_do_not_enter_personal_advice(self, taxonomy) -> None:
        rules = KeywordRules(
            schema_version=1,
            rules_version="test",
            min_job_count=1,
            min_company_count=1,
            max_keywords=80,
            stopwords=frozenset({"熟悉", "经验"}),
            aliases=(("redis", "Redis"),),
        )
        result = analyze_market(
            [make_row(requirements="熟悉 Redis，有生产环境经验")],
            taxonomy,
            profile=RecommendationProfile(skills=("Redis",)),
            keyword_rules=rules,
            as_of=date(2026, 7, 14),
        )

        candidate = next(
            item
            for item in result["keyword_analysis"]["keywords"]
            if item["kind"] == "candidate" and item["name"] == "Redis"
        )
        advice_text = json.dumps(result["personal_advice"], ensure_ascii=False)

        assert candidate["id"] not in advice_text

    def test_required_preferred_and_unspecified_are_separate(self, taxonomy) -> None:
        rows = [
            make_row(id=1, requirements="必须掌握 Python"),
            make_row(id=2, requirements="熟悉 RAG 者优先"),
            make_row(id=3, requirements="Docker、Kubernetes"),
        ]

        result = analyze_market(rows, taxonomy, as_of=date(2026, 7, 14))

        assert skill(result, "python")["required_count"] == 1
        assert skill(result, "rag")["preferred_count"] == 1
        assert skill(result, "docker")["unspecified_count"] == 1
        assert skill(result, "kubernetes")["unspecified_count"] == 1

    def test_ascii_alias_uses_word_boundaries(self, taxonomy) -> None:
        result = analyze_market(
            [make_row(requirements="参与 Google 平台建设，熟悉 Java")],
            taxonomy,
            as_of=date(2026, 7, 14),
        )

        assert skill(result, "go")["job_count"] == 0
        assert skill(result, "java")["job_count"] == 1


class TestMarketMetrics:
    def test_company_coverage_uses_companies_with_requirements(self, taxonomy) -> None:
        rows = [
            make_row(id=1, company_slug="a", company_name="A", requirements="Python"),
            make_row(id=2, company_slug="b", company_name="B", requirements="Java"),
            make_row(
                id=3,
                company_slug="c",
                company_name="C",
                requirements="",
                detail_completeness="responsibilities_only",
            ),
        ]

        result = analyze_market(rows, taxonomy, as_of=date(2026, 7, 14))
        python = skill(result, "python")

        assert python["company_count"] == 1
        assert python["company_denominator"] == 2
        assert python["company_coverage"] == 0.5

    def test_multi_location_counts_each_city_once(self, taxonomy) -> None:
        result = analyze_market(
            [make_row(location="北京市、Beijing、杭州")],
            taxonomy,
            as_of=date(2026, 7, 14),
        )

        assert result["quality"]["city_count"] == 2
        assert group(result, "location", "北京")["job_count"] == 1
        assert group(result, "location", "杭州")["job_count"] == 1
        assert result["sample"]["analyzed_jobs"] == 1

    def test_role_family_specificity_is_recomputable(self, taxonomy) -> None:
        rows = [
            make_row(
                id=1,
                title="应用安全工程师",
                matched_tags=["Security"],
                requirements="必须掌握 AppSec",
            ),
            make_row(
                id=2,
                title="云安全工程师",
                matched_tags=["Security"],
                requirements="必须掌握 Python",
            ),
        ]

        result = analyze_market(rows, taxonomy, as_of=date(2026, 7, 14))
        family = group(result, "role_family", "appsec_sdl")
        appsec = next(item for item in family["skills"] if item["id"] == "appsec")

        assert appsec["job_coverage"] == 1.0
        assert skill(result, "appsec")["job_coverage"] == 0.5
        assert appsec["specificity"] == 2.0

    def test_skill_combinations_count_jobs_not_occurrences(self, taxonomy) -> None:
        rows = [
            make_row(id=1, requirements="Python Python，Docker"),
            make_row(id=2, requirements="Python，Docker"),
        ]

        result = analyze_market(rows, taxonomy, as_of=date(2026, 7, 14))
        pair = next(
            item
            for item in result["skill_combinations"]
            if item["skill_ids"] == ["docker", "python"]
        )

        assert pair["job_count"] == 2
        assert pair["company_count"] == 1

    def test_new_job_windows_use_published_then_first_seen(self, taxonomy) -> None:
        rows = [
            make_row(id=1, published_at="2026-07-13T00:00:00"),
            make_row(id=2, published_at="", first_seen_at="2026-06-30T00:00:00"),
            make_row(id=3, published_at="2026-03-01T00:00:00"),
        ]

        result = analyze_market(rows, taxonomy, as_of=date(2026, 7, 14))

        assert result["new_jobs_by_window"] == {
            "7_days": 1,
            "30_days": 2,
            "90_days": 2,
            "unknown_date": 0,
        }


class TestProfileAdvice:
    def test_profile_skills_are_not_learning_gaps(self, taxonomy) -> None:
        profile = RecommendationProfile(
            skills=("Python",),
            target_roles=("AI安全工程师",),
            target_cities=("北京",),
        )
        rows = [
            make_row(id=1, requirements="必须掌握 Python 和 AppSec"),
            make_row(
                id=2,
                company_slug="other",
                company_name="Other",
                requirements="熟悉 Python 和 AppSec",
            ),
        ]

        result = analyze_market(
            rows,
            taxonomy,
            profile=profile,
            as_of=date(2026, 7, 14),
        )
        advice = result["personal_advice"]

        assert "python" in advice["covered_skill_ids"]
        assert "python" not in [
            item["skill_id"] for item in advice["learning_priorities"]
        ]
        assert advice["target_role_family_ids"] == ["ai_security"]
        assert any(item["skill_id"] == "python" for item in advice["resume_evidence"])

    def test_no_profile_omits_personal_advice(self, taxonomy) -> None:
        result = analyze_market([make_row()], taxonomy, as_of=date(2026, 7, 14))
        assert result["personal_advice"] is None

    def test_output_contains_no_profile_path_or_contact_data(self, taxonomy) -> None:
        profile = RecommendationProfile(
            skills=("Python",),
            constraints=("电话 13800138000", "邮箱 user@example.com"),
        )
        result = analyze_market(
            [make_row()],
            taxonomy,
            profile=profile,
            as_of=date(2026, 7, 14),
        )
        serialized = json.dumps(result, ensure_ascii=False)

        assert "13800138000" not in serialized
        assert "user@example.com" not in serialized
        assert "profile.md" not in serialized

    def test_profile_experience_is_compared_without_inference(self, taxonomy) -> None:
        profile = RecommendationProfile(skills=("AppSec",), experience_years=5)
        rows = [
            make_row(id=1, requirements="要求 3 年以上 AppSec 经验"),
            make_row(id=2, requirements="要求 8 年以上安全经验"),
            make_row(id=3, requirements="具备安全工程经验"),
        ]

        result = analyze_market(
            rows,
            taxonomy,
            profile=profile,
            as_of=date(2026, 7, 14),
        )

        assert result["personal_advice"]["experience_alignment"] == {
            "profile_experience_years": 5,
            "within_profile_experience_jobs": 1,
            "above_profile_experience_jobs": 1,
            "unknown_required_experience_jobs": 1,
        }

    def test_excluded_company_is_omitted_from_company_advice(self, taxonomy) -> None:
        profile = RecommendationProfile(
            skills=("Python",), excluded_companies=("Example",)
        )
        rows = [
            make_row(id=1, company_slug="example", company_name="Example"),
            make_row(id=2, company_slug="other", company_name="Other"),
        ]

        result = analyze_market(
            rows,
            taxonomy,
            profile=profile,
            as_of=date(2026, 7, 14),
        )
        advised = {
            item["company_slug"]
            for item in result["personal_advice"]["company_directions"]
        }
        assert "example" not in advised
        assert "other" in advised


class TestOutput:
    def test_deterministic_for_same_input(self, taxonomy) -> None:
        rows = [make_row(id=2), make_row(id=1)]
        first = analyze_market(rows, taxonomy, as_of=date(2026, 7, 14))
        second = analyze_market(rows, taxonomy, as_of=date(2026, 7, 14))
        assert first == second

    def test_markdown_renders_json_facts(self, taxonomy) -> None:
        result = analyze_market([make_row()], taxonomy, as_of=date(2026, 7, 14))
        markdown = render_market_markdown(result)

        assert "岗位市场需求画像" in markdown
        assert "有效岗位要求" in markdown
        assert str(result["quality"]["requirements_available_jobs"]) in markdown
        assert "未披露薪资" not in markdown
        assert "领域信号（不是具体技能）" in markdown
        assert "大模型领域提及" in markdown

    def test_markdown_marks_small_samples(self, taxonomy) -> None:
        result = analyze_market([make_row()], taxonomy, as_of=date(2026, 7, 14))
        markdown = render_market_markdown(result)
        assert "小样本" in markdown
