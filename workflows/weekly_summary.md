# Weekly Summary — AI Workflow Prompt

**Purpose**: Summarise the week's exported job facts into a brief report.

**Input**: An exported JSONL file from `findjobs export --since 7 --format jsonl --output reports/weekly/jobs.jsonl`

**Guardrails (MANDATORY — do not violate)**:
- Consume exported facts only. Do not fetch or invent jobs outside the exported data.
- Do not estimate undisclosed salary. If `salary_disclosed` is `false`, treat salary as unknown.
- Do not write to the database. This workflow is analysis-only.
- Do not hallucinate job details not present in the exported facts.

## Template

### 1. Load data

Read the JSONL file line by line. Each line is a JSON object with these fields:
`id`, `company_slug`, `company_name`, `title`, `location`, `job_type`, `status`,
`salary_text`, `salary_min`, `salary_max`, `salary_currency`, `salary_period`,
`salary_disclosed`, `matched_tags`, `url`, `first_seen_at`, `last_seen_at`, `published_at`.

### 2. Aggregate

Count total jobs, group by:
- Company
- Job type
- Location
- Matched tags (AI, Security, AI Security)

### 3. Summarise

Write a markdown report containing:
- **Overview**: total job count, date range (min first_seen_at → max last_seen_at).
- **Top Companies**: companies with the most active jobs.
- **Tag Distribution**: how many jobs carry each tag.
- **Salary Snapshot**: for disclosed-salary jobs, note the min/max range. If salary_disclosed=false for every job, state clearly that salary data is unavailable — do not guess.

### 4. Output

Save the report as `reports/weekly/<YYYY-MM-DD>-summary.md`.
