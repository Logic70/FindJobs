# FindJobs

A local job-collection application that collects jobs from official company
career pages, stores them in SQLite, and provides a local web UI plus export
files for AI-assisted analysis.

## Quick Start

```bash
uv sync
findjobs init
findjobs weekly --live
findjobs serve
```

Open the web UI at `http://127.0.0.1:8000/jobs`.

## Scope

- Collect official company career pages only.
- Do not collect Huawei or Huawei affiliates.
- Do not collect third-party job boards.
- Salary is stored only when the official source discloses it. Undisclosed
  salary is shown as `未披露`.
- AI jobs exclude title-level and job-type-level algorithm roles: if the title
  or job type contains `算法`, the job must not receive `AI` or `AI Security`
  tags.
- Algorithm roles are excluded entirely, including security algorithm roles.

## Export

Export collected jobs as structured data for analysis:

```bash
findjobs export
findjobs export --since 7 --format jsonl --output reports/weekly/jobs.jsonl
findjobs export --since 14 --format csv --output reports/weekly/jobs.csv
findjobs export --tag AI --status active --salary-disclosed true
findjobs export --company tencent --format jsonl
```

Export fields:

`id`, `company_slug`, `company_name`, `title`, `location`, `job_type`,
`status`, `salary_text`, `salary_min`, `salary_max`, `salary_currency`,
`salary_period`, `salary_disclosed`, `matched_tags`, `url`,
`first_seen_at`, `last_seen_at`, `published_at`.

No salary estimation or inference is performed. If the source did not disclose
a salary, `salary_disclosed` is `false` and the numeric fields are empty.

## AI Workflows

Workflow prompt templates are provided in `workflows/`:

| Prompt | Purpose |
|---|---|
| `workflows/weekly_summary.md` | Summarise a week's job facts |
| `workflows/match_analysis.md` | Match jobs against your profile |
| `workflows/priority_ranking.md` | Rank matches for application priority |
| `workflows/career_advice.md` | Produce development and learning advice |
| `workflows/adapter_repair.md` | Diagnose adapter failures from logs |

### Manual Usage

1. Initialize your local profile and fill in your background, target cities,
   salary expectations, and preferences:

   ```bash
   findjobs profile init
   ```
2. Export job facts:

   ```bash
   findjobs export --since 7 --format jsonl --output reports/weekly/jobs.jsonl
   ```

3. Run the deterministic local analysis:

   ```bash
   findjobs weekly --live
   ```

   Outputs:

   - `reports/weekly/<date>-summary.md`
   - `reports/weekly/ai-security.jsonl`
   - `reports/weekly/<date>-analysis-manifest.json`
   - `reports/match/<date>-matches.md` and
     `reports/priority/<date>-priorities.md` when `profile/profile.md` exists
   - `reports/match/<date>-career-advice.md` when `profile/profile.md` exists
   - `reports/match/<date>-profile-needed.md` when the real profile is missing

4. Use the workflow templates with the exported file. The prompts require the
   AI to consume only exported facts, never invent jobs, never estimate
   undisclosed salary, and never write to the database.

### Windows CMD + opencode

For the weekly report workflow on Windows, run from `cmd.exe`:

```cmd
tools\run_weekly_opencode.cmd
```

The script exports `reports\weekly\jobs.jsonl`, runs local weekly analysis,
invokes opencode, and writes AI output to
`reports\weekly\opencode-weekly-output.md`.

The script lets opencode use the model configured in your local opencode
session. In the current setup that default is deepseek-v4-flash, so no model
argument is required.

If opencode is blocked by quota or rate limits, inspect:

```cmd
opencode debug paths
```

Then open the `log` path shown by opencode. Provider errors such as
`Monthly usage limit reached` or `Rate limit exceeded` indicate an opencode
account/provider limit, not a FindJobs collection or export failure.

### Windows CMD + claude

For the same weekly report workflow using `claude` (Claude Code CLI), run from
`cmd.exe`:

```cmd
tools\run_weekly_claude.cmd
```

The script runs the deterministic local analysis, then invokes `claude -p` with
`--model deepseek-v4-flash[1M]` and asks Claude to read
`workflows\weekly_summary.md` plus `reports\weekly\jobs.jsonl` directly from
the repository. AI output is written to
`reports\weekly\claude-weekly-output.md`.

The Claude call is analysis-only — it reads exported facts and produces a
report without modifying files or the database.

If `claude` is not found, ensure the Claude Code CLI is installed and available
on `PATH`. For edit delegation, prefer the WSL fallback documented in
`docs/claude-code-status.md` until the Windows edit smoke also passes.

### Workflow Guardrails

Every workflow template enforces these rules:

- Consume exported facts only.
- Do not fetch or invent jobs outside the data.
- Do not estimate undisclosed salary.
- Do not write to the database.
- Do not hallucinate job facts.

## Config

Edit `config/sources.yaml` to add or enable sources. By default Tencent,
Baidu, ByteDance, Kuaishou, Xiaomi, Meituan, Ant Group, JD, NetEase, iFlyTek,
DeepSeek, Z.ai/Zhipu, Moonshot AI/Kimi, MiniMax, 01.AI, Baichuan AI,
ModelBest, SenseTime, and verified Alibaba business sub-sites are enabled
because their official or official-linked ATS endpoints have adapter tests and
live smoke coverage. The Alibaba central talent page stays inactive because it
is an official directory; the verified sub-site sources collect the jobs. Set
`is_active: true` on a source to enable it for `collect --live`.

Use `findjobs sources` to audit configured source coverage.

The config loader rejects entries referencing Huawei.

## Troubleshooting

### No Data In The Web UI

The web UI shows persisted jobs. If the database is empty, run:

```bash
findjobs init
findjobs weekly --live
```

Then refresh `http://127.0.0.1:8000/jobs`. If a source fails, its error is
printed to the console and logged in the `collect_runs` table.

`collect --live` prints a `collecting...` line before each source. Large
official ATS sources such as ByteDance/Feishu can take several minutes because
the adapter paginates shared AI/Security keywords and deduplicates the result
before storing only relevant jobs.

### Weekly Schedule

Preview the Windows scheduled task command:

```cmd
findjobs schedule install
```

By default this schedules the full weekly workflow. The generated task action
enters this project directory and runs `uv run findjobs weekly --live`, so it
does not depend on Task Scheduler finding the `findjobs` console script on
`PATH`. Use `--collect-only` only when you want collection without
export/analysis.

## Development

```bash
uv sync --group dev
pytest
```

For parallel company-adapter work, use
`docs/parallel-adapter-tasks.md` as the task boundary and integration gate.

## License

MIT
