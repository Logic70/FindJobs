"""Canonical job responsibility/requirement normalization.

Provides :class:`NormalizedJobDetails`, :func:`normalize_job_details`, and
:func:`compute_detail_completeness` to split job descriptions into
responsibilities and requirements based on recognised section headings,
and to derive the ``detail_completeness`` enum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Recognised section headings (Chinese and English)
# ---------------------------------------------------------------------------

_RESPONSIBILITY_HEADINGS = frozenset({
    "岗位职责",
    "工作职责",
    "职位描述",
    "岗位描述",
    "职责",
    "Responsibilities",
    "What you'll do",
})

_REQUIREMENT_HEADINGS = frozenset({
    "任职要求",
    "岗位要求",
    "职位要求",
    "资格要求",
    "任职资格",
    "要求",
    "Requirements",
    "Qualifications",
    "What we're looking for",
})

# ---------------------------------------------------------------------------
# Heading line compilation
#
# Each heading candidate line must MATCH THE WHOLE LINE to be recognised.
# This prevents "heading-like words in ordinary prose do not split" (e.g.
# "我们要求候选人" contains "要求" inline but does not match the pattern).
# ---------------------------------------------------------------------------

_PREFIX = r"^\s*(?:#+\s*)?(?:\d+\s*[.)、]\s*)?"
_SUFFIX = r"[：:\s]*$"

_RESP_HEADING_RE = re.compile(
    _PREFIX + "(?:" + "|".join(re.escape(h) for h in _RESPONSIBILITY_HEADINGS) + ")" + _SUFFIX,
    re.IGNORECASE,
)

_REQ_HEADING_RE = re.compile(
    _PREFIX + "(?:" + "|".join(re.escape(h) for h in _REQUIREMENT_HEADINGS) + ")" + _SUFFIX,
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Immutable result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedJobDetails:
    """Canonical, immutable result of detail normalization.

    Attributes:
        responsibilities: Canonical responsibility text (empty if none).
        requirements: Canonical requirement text (empty if none).
        detail_completeness: One of ``"full"``, ``"responsibilities_only"``,
            ``"requirements_only"``, ``"combined_only"``, ``"missing"``.
        description: The original description text, unchanged.
    """

    responsibilities: str
    requirements: str
    detail_completeness: str
    description: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_headings(description: str) -> list[tuple[int, str]]:
    """Find all heading lines and their types.

    Returns a list of ``(line_index, heading_type)`` tuples where
    *heading_type* is ``"responsibilities"`` or ``"requirements"``.
    """
    headings: list[tuple[int, str]] = []
    for i, line in enumerate(description.split("\n")):
        if _RESP_HEADING_RE.match(line):
            headings.append((i, "responsibilities"))
        elif _REQ_HEADING_RE.match(line):
            headings.append((i, "requirements"))
    return headings


def _extract_section_from(
    description: str,
    heading_idx: int,
    next_heading_idx: int | None,
) -> str:
    """Extract text after a heading line up to (but not including) the next."""
    lines = description.split("\n")
    if next_heading_idx is not None:
        section_lines = lines[heading_idx + 1 : next_heading_idx]
    else:
        section_lines = lines[heading_idx + 1 :]
    return "\n".join(section_lines).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_detail_completeness(
    description: str,
    responsibilities: str,
    requirements: str,
) -> str:
    """Derive the ``detail_completeness`` enum from canonical fields.

    Completeness values (exactly one of):

    * ``"full"`` -- both responsibilities and requirements are non-empty.
    * ``"responsibilities_only"`` -- only responsibilities is non-empty.
    * ``"requirements_only"`` -- only requirements is non-empty.
    * ``"combined_only"`` -- a description exists but no safe split was found.
    * ``"missing"`` -- all three fields are empty.
    """
    has_resp = bool(responsibilities.strip())
    has_req = bool(requirements.strip())
    has_desc = bool(description.strip())

    if has_resp and has_req:
        return "full"
    elif has_resp:
        return "responsibilities_only"
    elif has_req:
        return "requirements_only"
    elif has_desc:
        return "combined_only"
    else:
        return "missing"


def normalize_job_details(
    description: str,
    responsibilities: str = "",
    requirements: str = "",
) -> NormalizedJobDetails:
    """Normalize a job description into canonical responsibilities/requirements.

    **Override rule**: explicitly provided values always win for their
    respective field.  For any missing field, the original *description* is
    split using recognised section headings.

    **Source-order rule**: if both responsibility and requirement headings are
    found, they are populated in source order.  Text before the *first*
    recognised heading remains only in *description*.

    Args:
        description: The original, unmodified job description text.
        responsibilities: Explicit responsibility text (empty to infer).
        requirements: Explicit requirement text (empty to infer).

    Returns:
        A frozen :class:`NormalizedJobDetails` with all fields resolved.
    """
    result_resp = responsibilities.strip()
    result_req = requirements.strip()

    # -- Fast path: both fields already provided -------------------------------
    if result_resp and result_req:
        return NormalizedJobDetails(
            responsibilities=result_resp,
            requirements=result_req,
            detail_completeness=compute_detail_completeness(
                description, result_resp, result_req
            ),
            description=description,
        )

    # -- Try to split the description -----------------------------------------
    headings = _find_headings(description)

    if not headings:
        completeness = compute_detail_completeness(description, result_resp, result_req)
        return NormalizedJobDetails(
            responsibilities=result_resp,
            requirements=result_req,
            detail_completeness=completeness,
            description=description,
        )

    # We have at least one heading.
    first_type = headings[0][1]
    first_idx = headings[0][0]

    # Look for a subsequent heading of the *other* type.
    second: tuple[int, str] | None = None
    for idx, htype in headings[1:]:
        if htype != first_type:
            second = (idx, htype)
            break

    if second is not None:
        # Both heading types found -- split in source order.
        first_content = _extract_section_from(description, first_idx, second[0])
        second_content = _extract_section_from(description, second[0], None)

        if first_type == "responsibilities":
            resp_from_desc, req_from_desc = first_content, second_content
        else:
            req_from_desc, resp_from_desc = first_content, second_content

        if not result_resp:
            result_resp = resp_from_desc
        if not result_req:
            result_req = req_from_desc
    else:
        # Only one heading type present.
        content = _extract_section_from(description, first_idx, None)
        if first_type == "responsibilities" and not result_resp:
            result_resp = content
        elif first_type == "requirements" and not result_req:
            result_req = content

    completeness = compute_detail_completeness(description, result_resp, result_req)
    return NormalizedJobDetails(
        responsibilities=result_resp,
        requirements=result_req,
        detail_completeness=completeness,
        description=description,
    )
