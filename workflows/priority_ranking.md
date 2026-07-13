# Priority Ranking — Application Action Over Deterministic Tiers

**Purpose**: Explain application actions (apply-first, monitor, skip) for the
deterministic recommendation tiers, preserving the engine's score order and
tier assignments.  This workflow does **not** produce new scores or ranks —
it adds context for action planning.

## Inputs (all three required)

1. **`reports/match/jobs-full.jsonl`** — Full-format exported job facts
   (produced with `detail_level=full`).  Each row contains:
   - `responsibilities`, `requirements`, `detail_completeness`
   - `classification_version`, `classification_reasons`
   - `relevance_status`, `description`
   - `matched_tags`, `url`, salary fields

2. **`reports/match/recommendations.json`** — Deterministic recommendation
   scores and tiers from the local engine.  The score order and tier
   assignments are **authoritative** — they must not be changed.

3. **`profile/profile.json`** (or **`profile/profile.md`** when JSON is
   absent) — User background, skills, target cities, preferences, and
   excluded companies.

## Guardrails (MANDATORY — do not violate)

- **Preserve deterministic engine order and tier assignments.**  Do not
  re-score, re-rank, or re-order recommendations.  The `total_score` and
  `tier` from `recommendations.json` are the authority.
- Consume **only** the three listed inputs.  Use exported facts only — do not fetch or invent jobs
  outside the exported data.
- **Never estimate undisclosed salary.**  Do not estimate any salary — if
  `salary_disclosed` is `false`, treat salary as unknown.
- **Do not infer missing requirements.**  Missing requirements stay unknown.
  Use the official URL for verification.
- **Do not write to the database.**  This workflow is analysis-only.
- **Separate action advice from facts.**  Label application suggestions as
  commentary.

## Template

### 1. Load inputs

Read all three inputs.  Group recommendations by `tier` (high / medium /
exploratory), preserving the engine's intra-tier sort order (score
descending, then job ID descending).  Do **not** re-sort.

### 2. Application actions per tier

For each tier, suggest concrete actions:

- **high** (score ≥ 75): Apply first — discuss salary based on disclosed
  field; verify missing requirements at the official URL.
- **medium** (score ≥ 55): Good fit — monitor for changes; apply if time
  permits.
- **exploratory** (score < 55): Low priority — consider only if aligned
  with growth direction; salary disclosure and requirement coverage may be
  weak.

### 3. Salary disclosure notes

For each recommendation, note whether salary was disclosed.  Never estimate
a disclosed or undisclosed figure.

### 4. Output

Save as `reports/priority/<YYYY-MM-DD>-priorities.md` with:
- Tiered tables showing score, company, title, location, salary status, URL.
- Action notes (commentary only, not fact).
- Fact-boundary note.

Append a fact-boundary note:
```
_Fact boundary: Tiers and order are from the deterministic recommendation
engine.  Application actions are suggestions, not job requirements.  No
salary estimation was performed.  Missing requirements are not inferred.
Verify all details via the official URL._
```
