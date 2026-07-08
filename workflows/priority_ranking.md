# Priority Ranking — AI Workflow Prompt

**Purpose**: Rank matched jobs by priority for application.

**Input**: The match analysis report from `reports/match/`.

**Guardrails (MANDATORY — do not violate)**:
- Consume exported facts only. Do not fetch or invent jobs outside the exported data.
- Do not estimate undisclosed salary. If `salary_disclosed` is `false`, treat salary as unknown.
- Do not write to the database. This workflow is analysis-only.
- Do not hallucinate job details not present in the exported facts.

## Template

### 1. Read match results

Load the latest match report from `reports/match/`.

### 2. Rank

Reorder by:
1. **Match score** (higher first).
2. **Salary alignment** — prefer disclosed-salary jobs that fit the expected range.
3. **Tag priority** — jobs matching both AI and Security tags rank above single-tag jobs.
4. **Location fit** — target-city jobs rank above remote-flexible, which rank above non-target.

### 3. Categorise

Split into three tiers:
- **Top priority** (apply first).
- **Good fit** (apply if time permits).
- **Low priority** (weak match).

### 4. Output

Save as `reports/priority/<YYYY-MM-DD>-priorities.md` with tiered tables and a recommended action plan.
