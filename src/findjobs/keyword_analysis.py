"""Deterministic keyword discovery over normalized market-analysis jobs."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import jieba
import yaml


class KeywordAnalysisError(ValueError):
    """Raised when keyword rules or analysis input are invalid."""


@dataclass(frozen=True)
class KeywordRules:
    schema_version: int
    rules_version: str
    min_job_count: int
    min_company_count: int
    max_keywords: int
    stopwords: frozenset[str]
    aliases: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class KeywordDefinition:
    id: str
    name: str
    kind: str
    category: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class KeywordDocument:
    job_id: str
    title: str
    company_key: str
    company_name: str
    role_family_key: str
    role_family_name: str
    locations: tuple[str, ...]
    requirements: str
    responsibilities: str
    requirement_skill_ids: frozenset[str]
    requirement_domain_signal_ids: frozenset[str]
    work_skill_ids: frozenset[str]
    work_domain_signal_ids: frozenset[str]


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_PURE_NUMBER_RE = re.compile(r"^[\d._+%/-]+$")
_TECH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:C\+\+|C#|\.NET|[A-Za-z][A-Za-z0-9]*"
    r"(?:[.+/#:-][A-Za-z0-9+]+)*)(?![A-Za-z0-9])"
)
_BUILTIN_STOPWORDS = frozenset(
    {
        "and",
        "or",
        "with",
        "for",
        "from",
        "the",
        "a",
        "an",
        "to",
        "of",
        "in",
        "on",
        "is",
        "are",
        "及",
        "与",
        "和",
        "等",
        "或",
        "的",
        "并",
    }
)


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _required_string(raw: Any, field: str, path: Path) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise KeywordAnalysisError(f"{path}: {field} must be a non-empty string")
    return raw.strip()


def _required_positive_int(raw: Any, field: str, path: Path) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise KeywordAnalysisError(f"{path}: {field} must be a positive integer")
    return raw


def load_keyword_rules(path: Path) -> KeywordRules:
    """Load and validate versioned keyword-discovery rules."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KeywordAnalysisError(f"Keyword rules not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise KeywordAnalysisError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise KeywordAnalysisError(f"{path}: rules root must be an object")
    if raw.get("schema_version") != 1:
        raise KeywordAnalysisError(f"{path}: unsupported schema_version")

    stopwords_raw = raw.get("stopwords")
    if not isinstance(stopwords_raw, list) or not stopwords_raw:
        raise KeywordAnalysisError(f"{path}: stopwords must be a non-empty list")
    stopwords = frozenset(
        _normalized(_required_string(value, "stopwords[]", path))
        for value in stopwords_raw
    )

    aliases_raw = raw.get("aliases", {})
    if not isinstance(aliases_raw, dict):
        raise KeywordAnalysisError(f"{path}: aliases must be an object")
    aliases: list[tuple[str, str]] = []
    seen_aliases: dict[str, str] = {}
    for canonical_raw, values in aliases_raw.items():
        canonical = _required_string(canonical_raw, "aliases key", path)
        if not isinstance(values, list) or not values:
            raise KeywordAnalysisError(
                f"{path}: aliases.{canonical} must be a non-empty list"
            )
        for alias_raw in [canonical, *values]:
            alias = _required_string(alias_raw, f"aliases.{canonical}[]", path)
            key = _normalized(alias)
            existing = seen_aliases.get(key)
            if existing is not None and existing != canonical:
                raise KeywordAnalysisError(
                    f"{path}: alias {alias!r} maps to both {existing!r} and {canonical!r}"
                )
            seen_aliases[key] = canonical
    aliases.extend(sorted(seen_aliases.items()))

    return KeywordRules(
        schema_version=1,
        rules_version=_required_string(raw.get("rules_version"), "rules_version", path),
        min_job_count=_required_positive_int(
            raw.get("min_job_count"), "min_job_count", path
        ),
        min_company_count=_required_positive_int(
            raw.get("min_company_count"), "min_company_count", path
        ),
        max_keywords=_required_positive_int(
            raw.get("max_keywords"), "max_keywords", path
        ),
        stopwords=stopwords,
        aliases=tuple(aliases),
    )


def default_keyword_rules() -> KeywordRules:
    """Return deterministic defaults for direct library calls and tests."""
    return KeywordRules(
        schema_version=1,
        rules_version="builtin-1",
        min_job_count=5,
        min_company_count=2,
        max_keywords=80,
        stopwords=frozenset(
            {
                "负责",
                "参与",
                "相关",
                "具备",
                "熟悉",
                "掌握",
                "能力",
                "经验",
                "优先",
                "要求",
                "工作",
                "项目",
                "业务",
                "技术",
                "系统",
                "平台",
                "服务",
                "开发",
                "研发",
                "设计",
                "建设",
                "团队",
            }
        ),
        aliases=(),
    )


class _CandidateTokenizer:
    def __init__(
        self,
        definitions: tuple[KeywordDefinition, ...],
        rules: KeywordRules,
    ) -> None:
        self._tokenizer = jieba.Tokenizer()
        self._alias_names = dict(rules.aliases)
        self._formal_aliases: set[str] = set()
        self._stopwords = {
            *(_normalized(item) for item in rules.stopwords),
            *(_normalized(item) for item in _BUILTIN_STOPWORDS),
        }
        phrases: set[str] = set()
        for definition in definitions:
            for alias in {definition.name, *definition.aliases}:
                normalized_alias = _normalized(alias)
                self._formal_aliases.add(normalized_alias)
                phrases.add(alias)
        for alias, canonical in rules.aliases:
            phrases.add(alias)
            phrases.add(canonical)
        for phrase in sorted(phrases, key=lambda item: (-len(item), item.casefold())):
            self._tokenizer.add_word(phrase, freq=2_000_000)

    def candidates(self, text: str) -> dict[str, str]:
        if not text.strip():
            return {}
        normalized_text = unicodedata.normalize("NFKC", text)
        raw_tokens = set(self._tokenizer.cut(normalized_text, cut_all=False))
        raw_tokens.update(_TECH_TOKEN_RE.findall(normalized_text))

        lowered_text = _normalized(normalized_text)
        candidates: dict[str, str] = {}
        for alias, canonical in self._alias_names.items():
            if alias and alias in lowered_text:
                candidates[_normalized(canonical)] = canonical
        for raw_token in raw_tokens:
            token = unicodedata.normalize("NFKC", raw_token).strip(
                " \t\r\n,，.;；:：、()（）[]【】{}<>《》\"'`|!"
            )
            if not token:
                continue
            key = _normalized(token)
            canonical = self._alias_names.get(key)
            if canonical is not None:
                key = _normalized(canonical)
                token = canonical
            if not self._is_candidate(key):
                continue
            display = token if _CJK_RE.search(token) else token.casefold()
            existing = candidates.get(key)
            if existing is None or (display.casefold(), display) < (
                existing.casefold(),
                existing,
            ):
                candidates[key] = display
        return candidates

    def _is_candidate(self, key: str) -> bool:
        if key in self._formal_aliases or key in self._stopwords:
            return False
        if _PURE_NUMBER_RE.fullmatch(key):
            return False
        if _CJK_RE.search(key) and len(key) == 1:
            return False
        if not _CJK_RE.search(key) and len(key) < 2:
            return False
        return any(character.isalnum() or _CJK_RE.match(character) for character in key)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _candidate_id(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"candidate_{digest[:20]}"


def _definition_ids(document: KeywordDocument, kind: str) -> frozenset[str]:
    if kind == "skill":
        return document.requirement_skill_ids
    return document.requirement_domain_signal_ids


def _work_definition_ids(document: KeywordDocument, kind: str) -> frozenset[str]:
    if kind == "skill":
        return document.work_skill_ids
    return document.work_domain_signal_ids


def _distribution(
    matching_documents: list[KeywordDocument],
    all_documents: list[KeywordDocument],
    dimension: str,
) -> list[dict[str, Any]]:
    def memberships(document: KeywordDocument) -> tuple[tuple[str, str], ...]:
        if dimension == "company":
            return ((document.company_key, document.company_name),)
        if dimension == "role_family":
            return ((document.role_family_key, document.role_family_name),)
        return tuple((location, location) for location in document.locations)

    group_sizes: Counter[tuple[str, str]] = Counter()
    matching_counts: Counter[tuple[str, str]] = Counter()
    for document in all_documents:
        group_sizes.update(set(memberships(document)))
    for document in matching_documents:
        matching_counts.update(set(memberships(document)))

    rows = [
        {
            "key": key,
            "name": name,
            "job_count": count,
            "share_of_keyword": _ratio(count, len(matching_documents)),
            "group_job_count": group_sizes[(key, name)],
            "group_coverage": _ratio(count, group_sizes[(key, name)]),
        }
        for (key, name), count in matching_counts.items()
    ]
    rows.sort(key=lambda item: (-item["job_count"], item["name"], item["key"]))
    return rows


def _examples(documents: Iterable[KeywordDocument]) -> list[dict[str, Any]]:
    ordered = sorted(
        documents,
        key=lambda item: (item.company_name, item.title, item.job_id),
    )
    return [
        {
            "job_id": document.job_id,
            "title": document.title,
            "company_name": document.company_name,
            "locations": list(document.locations),
        }
        for document in ordered[:3]
    ]


def analyze_keywords(
    documents: Iterable[KeywordDocument],
    definitions: Iterable[KeywordDefinition],
    rules: KeywordRules,
) -> dict[str, Any]:
    """Discover keywords and calculate deterministic cross-section distributions."""
    ordered_documents = sorted(
        documents,
        key=lambda item: (item.company_key, item.job_id, item.title),
    )
    requirement_documents = [item for item in ordered_documents if item.requirements]
    ordered_definitions = tuple(sorted(definitions, key=lambda item: item.id))
    for definition in ordered_definitions:
        if definition.kind not in {"skill", "domain_signal"}:
            raise KeywordAnalysisError(
                f"Unsupported keyword definition kind: {definition.kind}"
            )
    tokenizer = _CandidateTokenizer(ordered_definitions, rules)

    requirement_candidate_tokens: dict[str, dict[str, str]] = {}
    work_candidate_tokens: dict[str, dict[str, str]] = {}
    candidate_documents: defaultdict[str, list[KeywordDocument]] = defaultdict(list)
    candidate_names: defaultdict[str, set[str]] = defaultdict(set)
    for document in requirement_documents:
        tokens = tokenizer.candidates(document.requirements)
        requirement_candidate_tokens[document.job_id] = tokens
        for key, name in tokens.items():
            candidate_documents[key].append(document)
            candidate_names[key].add(name)
    for document in ordered_documents:
        work_candidate_tokens[document.job_id] = tokenizer.candidates(
            document.responsibilities
        )

    accepted_candidates: dict[str, tuple[str, list[KeywordDocument]]] = {}
    for key, matching in candidate_documents.items():
        companies = {item.company_key for item in matching}
        if (
            len(matching) >= rules.min_job_count
            and len(companies) >= rules.min_company_count
        ):
            accepted_candidates[key] = (sorted(candidate_names[key])[0], matching)

    raw_rows: list[tuple[dict[str, Any], list[KeywordDocument], str | None]] = []
    definition_by_id = {definition.id: definition for definition in ordered_definitions}
    for definition in ordered_definitions:
        matching = [
            document
            for document in requirement_documents
            if definition.id in _definition_ids(document, definition.kind)
        ]
        if not matching:
            continue
        work_count = sum(
            definition.id in _work_definition_ids(document, definition.kind)
            for document in ordered_documents
        )
        raw_rows.append(
            (
                {
                    "id": definition.id,
                    "name": definition.name,
                    "kind": definition.kind,
                    "category": definition.category,
                    "job_count": len(matching),
                    "job_denominator": len(requirement_documents),
                    "job_coverage": _ratio(len(matching), len(requirement_documents)),
                    "company_count": len({item.company_key for item in matching}),
                    "work_content_job_count": work_count,
                },
                matching,
                None,
            )
        )

    for key, (name, matching) in accepted_candidates.items():
        raw_rows.append(
            (
                {
                    "id": _candidate_id(key),
                    "name": name,
                    "kind": "candidate",
                    "category": "候选关键词",
                    "job_count": len(matching),
                    "job_denominator": len(requirement_documents),
                    "job_coverage": _ratio(len(matching), len(requirement_documents)),
                    "company_count": len({item.company_key for item in matching}),
                    "work_content_job_count": sum(
                        key in work_candidate_tokens.get(document.job_id, {})
                        for document in ordered_documents
                    ),
                },
                matching,
                key,
            )
        )

    kind_order = {"skill": 0, "domain_signal": 1, "candidate": 2}
    raw_rows.sort(
        key=lambda item: (
            -item[0]["job_count"],
            kind_order[item[0]["kind"]],
            item[0]["name"].casefold(),
            item[0]["id"],
        )
    )
    selected = raw_rows[: rules.max_keywords]
    selected_ids = {row["id"] for row, _, _ in selected}
    candidate_id_by_key = {
        key: row["id"] for row, _, key in selected if key is not None
    }

    per_document_ids: dict[str, set[str]] = defaultdict(set)
    for document in requirement_documents:
        for definition_id in {
            *document.requirement_skill_ids,
            *document.requirement_domain_signal_ids,
        }:
            if definition_id in selected_ids and definition_id in definition_by_id:
                per_document_ids[document.job_id].add(definition_id)
        for key in requirement_candidate_tokens.get(document.job_id, {}):
            candidate_id = candidate_id_by_key.get(key)
            if candidate_id is not None:
                per_document_ids[document.job_id].add(candidate_id)

    names = {row["id"]: row["name"] for row, _, _ in selected}
    kinds = {row["id"]: row["kind"] for row, _, _ in selected}
    cooccurrence: Counter[tuple[str, str]] = Counter()
    for keyword_ids in per_document_ids.values():
        for left in keyword_ids:
            for right in keyword_ids:
                if left != right:
                    cooccurrence[(left, right)] += 1

    rows: list[dict[str, Any]] = []
    for row, matching, _ in selected:
        related = [
            {
                "id": other_id,
                "name": names[other_id],
                "kind": kinds[other_id],
                "job_count": count,
                "share_of_keyword": _ratio(count, row["job_count"]),
            }
            for (source_id, other_id), count in cooccurrence.items()
            if source_id == row["id"] and count > 0
        ]
        related.sort(key=lambda item: (-item["job_count"], item["name"], item["id"]))
        row.update(
            {
                "distributions": {
                    dimension: _distribution(
                        matching,
                        requirement_documents,
                        dimension,
                    )
                    for dimension in ("company", "role_family", "location")
                },
                "related_keywords": related[:8],
                "example_jobs": _examples(matching),
            }
        )
        rows.append(row)

    return {
        "schema_version": 1,
        "rules_version": rules.rules_version,
        "source": "requirements",
        "job_denominator": len(requirement_documents),
        "candidate_thresholds": {
            "min_job_count": rules.min_job_count,
            "min_company_count": rules.min_company_count,
            "max_keywords": rules.max_keywords,
        },
        "keywords": rows,
        "fact_boundary": (
            "Candidates are discovered from requirements only. Responsibilities are "
            "reported separately and candidates never affect recommendations."
        ),
    }


def keyword_cloud_size(job_count: int, maximum: int) -> float:
    """Map document frequency to a stable relative size for HTML rendering."""
    if job_count <= 0 or maximum <= 0:
        return 1.0
    return round(1.0 + 1.5 * math.log1p(job_count) / math.log1p(maximum), 4)
