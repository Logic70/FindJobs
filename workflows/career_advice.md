# Career Advice — AI Commentary Over Exported Facts and Scores

**Purpose**: Derive development and learning advice from recurring explicit
requirements across exported job facts, deterministic scores, and the user's
profile.  Clearly separate advice from official job facts.

## Inputs (all three required)

1. **`reports/match/jobs-full.jsonl`** — Full-format exported job facts
   (produced with `detail_level=full`).  Each row contains:
   - `responsibilities`, `requirements`, `detail_completeness`
   - `classification_version`, `classification_reasons`
   - `relevance_status`, `description`
   - `matched_tags`, `url`, salary fields

2. **`reports/match/recommendations.json`** — Deterministic recommendation
   scores, tiers, aggregate learning advice, and per-job evidence from the
   local engine.  Scores and tiers are authoritative.

3. **`profile/profile.json`** (or **`profile/profile.md`** when JSON is
   absent) — User background, skills, target cities, preferences, and
   excluded companies.

## Guardrails (MANDATORY — do not violate)

- Consume **only** the three listed inputs.  Use exported facts only — do not fetch or invent jobs
  outside the exported data.
- **Never estimate undisclosed salary.**  Do not estimate any salary — if
  `salary_disclosed` is `false`, treat salary as unknown.
- **Do not infer missing requirements.**  Missing requirements stay unknown.
  Use the official URL as the source of truth.
- **Do not write to the database.**  This workflow is analysis-only.
- **Derive recurring gaps from explicit `requirements` text in
  `jobs-full.jsonl` and profile skills.**  Do not fabricate skill gaps
  that are not supported by the data.
- **Clearly separate advice from facts.**  Label development or learning
  advice as analysis, not as hidden job requirements.
- **Do not re-score, re-rank, or re-order recommendations.**  The engine
  scores are authoritative.

## Template

### 1. Load inputs

Read all three inputs.  Cross-reference the deterministic
`aggregate_learning_advice` from `recommendations.json` with explicit
`requirements` text from `jobs-full.jsonl`.

### 2. Identify recurring requirements

Scan `requirements` across high-scoring recommendations.  Look for skills
that appear in multiple job requirement texts.  Compare against profile
skills.  Clearly label matches and gaps.

### 3. Derive development advice

- For each recurring gap, suggest learning directions.
- Reference the official URL for full requirement verification.
- Do **not** present advice as job requirements.

### 4. Output

Save as `reports/match/<YYYY-MM-DD>-career-advice.md` with sections:
- Recommended job directions (based on engine tiers and tags)
- Development advice (from recurring requirement gaps)
- Learning advice (from profile skill gaps vs requirement signals)
- Fact-boundary notes

Append a fact-boundary note:
```
_Fact boundary: Above advice is analysis of exported job facts and
deterministic engine output, not hidden job requirements.  Always verify
requirements via the official URL.  No salary estimation was performed.
Missing requirements were not inferred._
```
