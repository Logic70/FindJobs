# Career Advice — AI Workflow Prompt

**Purpose**: Produce development and learning advice from exported FindJobs
facts and the user's profile.

**Input**:
- An exported JSONL file, such as `reports/weekly/jobs.jsonl`.
- `profile/profile.md` containing the user's background, preferences, and constraints.
- Optional deterministic reports from `reports/match/` and `reports/priority/`.

**Guardrails (MANDATORY — do not violate)**:
- Consume exported facts only. Do not fetch or invent jobs outside the exported data.
- Do not estimate undisclosed salary. If `salary_disclosed` is `false`, treat salary as unknown.
- Do not write to the database. This workflow is analysis-only.
- Do not hallucinate job details not present in the exported facts.
- Separate advice from facts: clearly label development or learning advice as analysis.

## Template

### 1. Load Inputs

Read the exported jobs and the user profile. Do not query the SQLite database.

### 2. Recommend Directions

Summarize the most relevant directions using only exported job titles, tags,
locations, salary disclosure flags, URLs, and profile preferences.

### 3. Identify Skill Gaps

Compare recurring exported job signals against profile skills. Suggest learning
items only as advice, not as hidden job requirements.

### 4. Output

Save as `reports/match/<YYYY-MM-DD>-career-advice.md` with:

- recommended job directions,
- development advice,
- learning advice,
- fact-boundary notes.
