"""Privacy-safe profile loading for deterministic recommendations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from findjobs.locations import normalize_location, split_locations
from findjobs.profile_import import Profile

_SECTION_RE = re.compile(r"^##\s+(.+)$")
_LABELED_VALUE_RE = re.compile(r"^-\s+\*\*(.+?)\*\*:\s*(.+)$")
_BULLET_RE = re.compile(r"^-\s+(.+)$")
_EXPERIENCE_YEARS_RE = re.compile(r"(\d+(?:\.\d+)?)")


# ---------------------------------------------------------------------------
# Immutable recommendation profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecommendationProfile:
    """Frozen immutable profile containing only recommendation-relevant fields.

    No PII, raw text, source metadata, or contact data is retained.
    """

    skills: tuple[str, ...] = ()
    experience_years: float | None = None
    roles: tuple[str, ...] = ()
    target_cities: tuple[str, ...] = ()
    target_roles: tuple[str, ...] = ()
    excluded_companies: tuple[str, ...] = ()
    work_types: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deduplicate_stable(items: list[str]) -> list[str]:
    """Deduplicate stably, case-insensitively, preserving first display spelling."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item.strip())
    return result


def _canonicalize_remote(value: str) -> str:
    """Canonicalize ``Remote`` / ``远程`` to ``remote``."""
    lower = value.strip().lower()
    if lower in ("remote", "远程"):
        return "remote"
    return value.strip()


def _canonicalize_target_cities(cities: list[str]) -> list[str]:
    """Canonicalize a list of city values (aliases, multi-city, remote)."""
    result: list[str] = []
    for city in cities:
        parts = split_locations(city)
        if parts:
            for p in parts:
                result.append(_canonicalize_remote(p))
        else:
            normalized = normalize_location(city)
            if normalized:
                result.append(_canonicalize_remote(normalized))
    return result


def _is_skippable(line: str) -> bool:
    """Return True if *line* should be ignored during section parsing."""
    stripped = line.strip()
    if not stripped:
        return True
    if stripped == "_Not specified._":
        return True
    if re.match(r"^[-*_]{3,}$", stripped):
        return True
    if stripped.startswith("_Contact information"):
        return True
    if stripped == "-":
        return True
    return False


def _parse_markdown_sections(text: str) -> dict[str, list[str]]:
    """Split markdown into ``##`` sections, returning ``{lowercase_heading: [lines]}``."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        m = _SECTION_RE.match(line)
        if m:
            current = m.group(1).strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _parse_background_section(lines: list[str]) -> dict:
    """Parse labeled values from the ``## Background`` section.

    Recognises ``Total experience`` / ``Experience``, ``Roles`` / ``Role``,
    and ``Skills`` labels in ``- **Label**: value`` format.
    """
    result: dict = {}
    for line in lines:
        if _is_skippable(line):
            continue
        m = _LABELED_VALUE_RE.match(line)
        if not m:
            continue
        label = m.group(1).strip()
        value = m.group(2).strip()

        lower = label.lower()
        if lower in ("total experience", "experience"):
            years_match = _EXPERIENCE_YEARS_RE.search(value)
            if years_match:
                result["experience_years"] = float(years_match.group(1))
        elif lower in ("roles", "role"):
            parts = [p.strip() for p in re.split(r"[，、,]", value) if p.strip()]
            result["roles"] = parts
        elif lower == "skills":
            parts = [p.strip() for p in re.split(r"[，、,]", value) if p.strip()]
            result["skills"] = parts
    return result


def _parse_bullet_section(lines: list[str]) -> list[str]:
    """Parse bare bullet points from a section, skipping ignored lines."""
    items: list[str] = []
    for line in lines:
        if _is_skippable(line):
            continue
        m = _BULLET_RE.match(line.strip())
        if m:
            items.append(m.group(1).strip())
    return items


def _parse_preferences_section(lines: list[str]) -> list[str]:
    """Parse Preferences section, supporting bare bullets and labeled values.

    Labeled bullets (``- **Job Type**: Full-time``) yield the value only.
    Bare bullets (``- Remote``) yield the whole text.
    """
    items: list[str] = []
    for line in lines:
        if _is_skippable(line):
            continue
        stripped = line.strip()
        # Labeled value: - **Label**: Value
        labeled = _LABELED_VALUE_RE.match(stripped)
        if labeled:
            items.append(labeled.group(2).strip())
            continue
        # Bare bullet: - Value
        bare = _BULLET_RE.match(stripped)
        if bare:
            items.append(bare.group(1).strip())
    return items


# ---------------------------------------------------------------------------
# JSON loader
# ---------------------------------------------------------------------------


def _load_recommendation_json(path: Path) -> RecommendationProfile:
    """Load a ``RecommendationProfile`` from a JSON file via ``Profile`` schema."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed JSON in {path}: {e}") from e

    try:
        profile = Profile.model_validate(data)
    except Exception as e:
        raise ValueError(f"Invalid profile schema in {path}: {e}") from e

    return RecommendationProfile(
        skills=tuple(_deduplicate_stable(profile.skills)),
        experience_years=profile.experience_years,
        roles=tuple(_deduplicate_stable(profile.roles)),
        target_cities=tuple(
            _deduplicate_stable(
                _canonicalize_target_cities(profile.target_cities)
            )
        ),
        target_roles=tuple(_deduplicate_stable(profile.target_roles)),
        excluded_companies=tuple(_deduplicate_stable(profile.excluded_companies)),
        work_types=tuple(
            _deduplicate_stable(
                _canonicalize_remote(wt) for wt in profile.work_types
            )
        ),
        constraints=tuple(_deduplicate_stable(profile.constraints)),
    )


# ---------------------------------------------------------------------------
# Markdown loader
# ---------------------------------------------------------------------------


def _load_recommendation_markdown(path: Path) -> RecommendationProfile:
    """Load a ``RecommendationProfile`` from a Markdown file."""
    text = path.read_text(encoding="utf-8")
    sections = _parse_markdown_sections(text)

    # Parse Background for labeled values (case-insensitive section key)
    background_lines = sections.pop("background", [])
    bg = _parse_background_section(background_lines)

    # Parse bullet sections (pop so unknown sections are ignored)
    # Section keys are lowercased by _parse_markdown_sections.
    target_cities_raw = _parse_bullet_section(sections.pop("target cities", []))
    target_roles = _parse_bullet_section(sections.pop("target roles", []))
    preferences = _parse_preferences_section(sections.pop("preferences", []))
    excluded_list = _parse_bullet_section(sections.pop("excluded companies", []))
    constraints_list = _parse_bullet_section(sections.pop("constraints", []))

    canonical_work_types = [_canonicalize_remote(wt) for wt in preferences]

    return RecommendationProfile(
        skills=tuple(_deduplicate_stable(bg.get("skills", []))),
        experience_years=bg.get("experience_years"),
        roles=tuple(_deduplicate_stable(bg.get("roles", []))),
        target_cities=tuple(
            _deduplicate_stable(_canonicalize_target_cities(target_cities_raw))
        ),
        target_roles=tuple(_deduplicate_stable(target_roles)),
        excluded_companies=tuple(_deduplicate_stable(excluded_list)),
        work_types=tuple(_deduplicate_stable(canonical_work_types)),
        constraints=tuple(_deduplicate_stable(constraints_list)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_recommendation_profile(path: Path) -> RecommendationProfile:
    """Load a ``RecommendationProfile`` from *path* (supports ``.json`` and ``.md`` only)."""
    if not path.exists():
        raise FileNotFoundError(f"Profile file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    ext = path.suffix.lower()
    if ext == ".json":
        return _load_recommendation_json(path)
    elif ext == ".md":
        return _load_recommendation_markdown(path)
    else:
        raise ValueError(
            f"Unsupported file extension: {ext!r} (expected .json or .md)"
        )
