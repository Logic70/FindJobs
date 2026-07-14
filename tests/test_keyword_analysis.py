from __future__ import annotations

from pathlib import Path

import pytest

from findjobs.keyword_analysis import (
    KeywordAnalysisError,
    KeywordDefinition,
    KeywordDocument,
    KeywordRules,
    analyze_keywords,
    load_keyword_rules,
)


RULES_PATH = Path(__file__).parents[1] / "config" / "keyword_rules.yaml"


def definition(
    term_id: str,
    name: str,
    kind: str = "skill",
    *aliases: str,
) -> KeywordDefinition:
    return KeywordDefinition(
        id=term_id,
        name=name,
        kind=kind,
        category="测试",
        aliases=(name, *aliases),
    )


def document(**overrides: object) -> KeywordDocument:
    values: dict[str, object] = {
        "job_id": "1",
        "title": "AI工程师",
        "company_key": "company-a",
        "company_name": "Company A",
        "role_family_key": "ai_application",
        "role_family_name": "AI应用工程",
        "locations": ("北京",),
        "requirements": "熟悉 Python 和 Redis，具备相关项目经验",
        "responsibilities": "使用 Kafka 建设平台",
        "requirement_skill_ids": frozenset({"python"}),
        "requirement_domain_signal_ids": frozenset(),
        "work_skill_ids": frozenset(),
        "work_domain_signal_ids": frozenset(),
    }
    values.update(overrides)
    return KeywordDocument(**values)


def rules(**overrides: object) -> KeywordRules:
    values: dict[str, object] = {
        "schema_version": 1,
        "rules_version": "test",
        "min_job_count": 2,
        "min_company_count": 2,
        "max_keywords": 20,
        "stopwords": frozenset({"熟悉", "具备", "相关", "项目", "经验", "平台"}),
        "aliases": (("redis", "Redis"),),
    }
    values.update(overrides)
    return KeywordRules(**values)


DEFINITIONS = (
    definition("python", "Python"),
    definition("kubernetes", "Kubernetes", "skill", "K8s"),
    definition("llm_domain", "大模型领域提及", "domain_signal", "大模型", "LLM"),
)


def keyword(result: dict, keyword_id: str) -> dict:
    return next(item for item in result["keywords"] if item["id"] == keyword_id)


def test_loads_versioned_keyword_rules() -> None:
    loaded = load_keyword_rules(RULES_PATH)

    assert loaded.schema_version == 1
    assert loaded.min_job_count == 5
    assert loaded.min_company_count == 2
    assert loaded.max_keywords == 60
    assert loaded.min_token_length == 2
    assert "大模型" in loaded.user_dict


def test_invalid_keyword_rules_fail_with_context(tmp_path: Path) -> None:
    path = tmp_path / "keyword-rules.yaml"
    path.write_text("schema_version: 9", encoding="utf-8")

    with pytest.raises(KeywordAnalysisError, match="unsupported schema_version"):
        load_keyword_rules(path)


def test_discovers_candidate_with_document_dedupe_and_distributions() -> None:
    documents = [
        document(requirements="熟悉 Python、Redis、Redis，具备相关项目经验"),
        document(
            job_id="2",
            company_key="company-b",
            company_name="Company B",
            role_family_key="ai_security",
            role_family_name="AI安全",
            locations=("上海", "北京"),
            requirements="掌握 redis 和 Python",
        ),
    ]

    result = analyze_keywords(documents, DEFINITIONS, rules())

    candidate = next(item for item in result["keywords"] if item["name"] == "Redis")
    assert candidate["kind"] == "candidate"
    assert candidate["job_count"] == 2
    assert candidate["company_count"] == 2
    assert candidate["job_coverage"] == 1.0
    assert candidate["work_content_job_count"] == 0
    assert candidate["distributions"]["company"][0]["job_count"] == 1
    assert {item["name"] for item in candidate["distributions"]["location"]} == {
        "上海",
        "北京",
    }


def test_formal_aliases_do_not_reappear_as_candidates() -> None:
    documents = [
        document(
            requirements="熟悉 K8s 和 Python",
            requirement_skill_ids=frozenset({"python", "kubernetes"}),
        ),
        document(
            job_id="2",
            company_key="company-b",
            company_name="Company B",
            requirements="Kubernetes 与 Python",
            requirement_skill_ids=frozenset({"python", "kubernetes"}),
        ),
    ]

    result = analyze_keywords(documents, DEFINITIONS, rules())

    assert keyword(result, "kubernetes")["job_count"] == 2
    assert not any(
        item["kind"] == "candidate" and item["name"].casefold() in {"k8s", "kubernetes"}
        for item in result["keywords"]
    )


def test_candidate_alias_cannot_duplicate_formal_skill() -> None:
    configured_rules = rules(aliases=(("golang", "Go"), ("go", "Go")))
    definitions = (*DEFINITIONS, definition("go", "Go", "skill", "Golang"))
    documents = [
        document(
            job_id=str(index),
            company_key=f"company-{index % 2}",
            requirements="熟悉 Golang",
            requirement_skill_ids=frozenset({"go"}),
        )
        for index in range(1, 3)
    ]

    result = analyze_keywords(documents, definitions, configured_rules)

    assert keyword(result, "go")["job_count"] == 2
    assert not any(
        item["kind"] == "candidate" and item["name"] == "Go"
        for item in result["keywords"]
    )


def test_responsibilities_only_tokens_do_not_create_candidates() -> None:
    documents = [
        document(requirements="熟悉 Python", responsibilities="使用 Kafka 和 gRPC"),
        document(
            job_id="2",
            company_key="company-b",
            company_name="Company B",
            requirements="掌握 Python",
            responsibilities="维护 Kafka 与 gRPC",
        ),
    ]

    result = analyze_keywords(documents, DEFINITIONS, rules())

    assert not any(item["kind"] == "candidate" for item in result["keywords"])


def test_candidate_work_count_is_separate_from_requirement_count() -> None:
    documents = [
        document(requirements="使用 Redis", responsibilities="建设 Redis 服务"),
        document(
            job_id="2",
            company_key="company-b",
            company_name="Company B",
            requirements="Redis 开发",
            responsibilities="无",
        ),
    ]

    result = analyze_keywords(documents, DEFINITIONS, rules())
    candidate = next(item for item in result["keywords"] if item["name"] == "Redis")

    assert candidate["job_count"] == 2
    assert candidate["work_content_job_count"] == 1


def test_stopwords_thresholds_and_single_character_fragments_are_filtered() -> None:
    documents = [
        document(requirements="负责 相关 能力 经验 云 Redis"),
        document(
            job_id="2",
            company_key="company-a",
            requirements="负责 Redis",
        ),
    ]

    result = analyze_keywords(documents, DEFINITIONS, rules())

    assert not any(item["kind"] == "candidate" for item in result["keywords"])


def test_related_keywords_use_per_job_cooccurrence() -> None:
    documents = [
        document(requirements="Python Redis Redis"),
        document(
            job_id="2",
            company_key="company-b",
            company_name="Company B",
            requirements="Python Redis",
        ),
    ]

    result = analyze_keywords(documents, DEFINITIONS, rules())
    candidate = next(item for item in result["keywords"] if item["name"] == "Redis")

    assert candidate["related_keywords"][0]["id"] == "python"
    assert candidate["related_keywords"][0]["job_count"] == 2


def test_output_is_deterministic_when_input_order_changes() -> None:
    documents = [
        document(requirements="Python Redis"),
        document(
            job_id="2",
            company_key="company-b",
            company_name="Company B",
            requirements="Python Redis",
        ),
    ]

    first = analyze_keywords(documents, DEFINITIONS, rules())
    second = analyze_keywords(list(reversed(documents)), DEFINITIONS, rules())

    assert first == second
