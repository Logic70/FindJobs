# Match Analysis — AI Workflow Prompt

**Purpose**: Match exported jobs against the user's profile (`profile/profile.md`) and score relevance.

**Input**:
- An exported JSONL file (e.g. `reports/weekly/jobs.jsonl`)
- `profile/profile.md` containing the user's background, preferences, and constraints.

**Guardrails (MANDATORY — do not violate)**:
- Consume exported facts only. Do not fetch or invent jobs outside the exported data.
- Do not estimate undisclosed salary. If `salary_disclosed` is `false`, treat salary as unknown.
- Do not write to the database. This workflow is analysis-only.
- Do not hallucinate job details not present in the exported facts.

## Template

### 1. Load profile

Read `profile/profile.md`. It contains:
- Background / skills
- Target cities
- Salary expectation (range)
- AI engineering / security preferences
- Excluded companies

### 2. Load exported jobs

Read the JSONL file line by line.

### 3. Score each job

For each job, assign:
- **match_score**: 0–100 based on how well it matches the profile.
- **match_reasons**: brief list of why it matches or doesn't.

Consider:
- Title / matched_tags alignment with skills and preferences.
- Location in target cities (or remote-friendly).
- Salary within expected range (only if `salary_disclosed` is `true`; otherwise skip salary comparison).
- Company not in excluded list.
- Job type (full-time, intern, etc.) matches preference.

### 4. Output

Save results as `reports/match/<YYYY-MM-DD>-matches.md` with a ranked table:

```markdown
| Score | Company | Title | Location | Salary | Tags |
|-------|---------|-------|----------|--------|------|
| 95    | Acme    | AI Engineer | Beijing | 30k-50k | AI |
```

Append a summary: how many jobs matched above 70, 50, and below 50.
