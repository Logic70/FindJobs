# Adapter Repair — AI Workflow Prompt

**Purpose**: Diagnose and propose fixes for failing adapter collection runs.

**Input**: Collection runner logs, fixture files, or adapter source code.

**Guardrails (MANDATORY — do not violate)**:
- Consume exported facts only. Do not fetch or invent jobs outside the exported data.
- Do not estimate undisclosed salary. If `salary_disclosed` is `false`, treat salary as unknown.
- Do not write to the database. This workflow is analysis-only.
- Do not hallucinate job details not present in the exported facts.

## Template

### 1. Gather context

Read:
- Adapter source (`src/findjobs/adapters/<company>.py`)
- Log output (stderr/stdout from `findjobs collect --live`)
- Fixture file if one was used for testing

### 2. Identify failure

Common failure modes:
- HTML structure changed (CSS selectors no longer match).
- API response format changed (JSON keys renamed, pagination changed).
- Network error (timeout, DNS, TLS).
- Data validation error (new field format breaks parser).

### 3. Propose fix

For each failure, describe:
- Root cause (be specific — line numbers, selectors).
- Required change to adapter code.
- How to test the fix (update fixture + re-run).

### 4. Output

Save report as `reports/adapter-repair/<YYYY-MM-DD>-<adapter-name>-repair.md`.
