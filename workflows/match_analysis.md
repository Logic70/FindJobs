# Match Analysis — AI Commentary Over Deterministic Scores

**Purpose**: Provide narrative commentary over the deterministic recommendation
scores from `reports/match/recommendations.json`, using full-export facts and
the user profile.  This workflow does **not** produce scores — it explains
them.

## Inputs (all three required)

1. **`reports/match/jobs-full.jsonl`** — Full-format exported job facts
   (produced with `detail_level=full`).  Each row contains:
   - `responsibilities`, `requirements`, `detail_completeness`
   - `classification_version`, `classification_reasons`
   - `relevance_status`, `description`
   - `matched_tags`, `url`, salary fields

2. **`reports/match/recommendations.json`** — Deterministic recommendation
   scores, tiers, and evidence from the local engine.  The scores and tiers
   in this file are **authoritative** — do not re-score, re-rank, or change
   them.

3. **`profile/profile.json`** (or **`profile/profile.md`** when JSON is
   absent) — User background, skills, target cities, preferences, and
   excluded companies.

## Guardrails (MANDATORY — do not violate)

- Consume **only** the three listed inputs.  Use exported facts only — do not fetch or invent jobs
  outside the exported data.
- **Do not re-score, re-rank, or re-order** recommendations.  The
  `recommendations.json` scores and tiers are authoritative.
- **Never estimate undisclosed salary.**  Do not estimate any salary — if
  `salary_disclosed` is `false`, treat salary as unknown.  Never infer a
  salary range.
- **Do not infer missing requirements.**  Use `responsibilities`,
  `requirements`, and `detail_completeness` as-is.  Missing requirements
  stay unknown.  The official URL is the source of truth.
- **Do not write to the database.**  This workflow is commentary only.
- **Separate commentary from facts.**  Clearly label analysis, suggestions,
  and observations as commentary.  Do not present narrative as official
  job data.
- **Do not add new fields to `recommendations.json` entries.**  Your output
  is a separate narrative file.

## Template

### 1. Load inputs

Read all three inputs.  Validate that `recommendations.json` exists and
contains `recommendations` with `total_score` and `tier` fields.

### 2. Commentary per recommendation

For each recommendation, explain the score using the evidence fields in
`recommendations.json`:
- Domain, skills, requirements, experience, and location components.
- Matched skills and gaps.
- How `detail_completeness` affects confidence in requirement coverage.
- Salary disclosure status (never estimate).

### 3. Summary observations

- Which domains (AI, Security, AI Security) are most represented.
- Which skill gaps recur across recommendations.
- Location vs target city patterns.

### 4. Output

Save commentary as a separate narrative file (e.g.
`reports/match/<YYYY-MM-DD>-match-commentary.md`).

Append a fact-boundary note:
```
_Fact boundary: This commentary is analysis over deterministic engine output
and exported job facts. Scores and tiers are from the local recommendation
engine. No salary estimation was performed. Missing requirements are not
inferred._
```
