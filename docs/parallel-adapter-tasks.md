# Parallel Adapter Task Packages

Use this file to run 2-3 company adapter tasks in parallel while keeping
integration and activation under Codex review.

## Shared Rules

- Each task owns exactly one company.
- Do not edit `config/sources.yaml`.
- Do not edit `src/findjobs/adapters/__init__.py`.
- Do not edit shared reports or database files.
- Do not enable `is_active: true`.
- Do not use third-party job boards.
- Do not estimate salary.
- Preserve the AI rule: title-level or job-type-level `算法` jobs must not
  receive `AI` or `AI Security`.
- Reuse `findjobs.adapters.keywords.TARGET_KEYWORDS` for target-keyword
  collection. Do not reintroduce one-off local keyword lists unless the source
  has a documented official term such as DeepSeek/Moka `AGI`.
- If live API probing fails three times, stop and report exact URLs, methods,
  payloads, status codes, and response snippets.

## Adapter Acceptance Checklist

Every new or changed adapter must explicitly pass these checks before Codex
may enable the source:

- Official source only: collect from the company's own careers page or an ATS
  page clearly linked by the company. Do not use third-party job boards.
- Huawei exclusion: do not add Huawei as a company/source. Mentions of Huawei
  hardware inside another company's requirement text are not company sources.
- Salary facts only: parse salary only when the official response explicitly
  discloses a salary field or visible salary text. Otherwise store undisclosed;
  never estimate from title, level, city, or market assumptions.
- AI scope: keep AI application, AI platform, MLOps, inference deployment,
  AI engineering, Agent/RAG/tool-use engineering, and AI security.
- Algorithm exclusion: if the title or job type contains `算法`, exclude the
  job entirely, including security algorithm roles.
- Security scope: keep AppSec, SDL, vulnerability, penetration/offense-defense,
  cloud security, data security, privacy, security operations, risk control,
  anti-fraud, and anti-cheat.
- Non-target exclusion: filter sales, channel, customer success, consulting,
  market, public affairs, generic operations, QA/testing, audit, data analyst,
  physical safety, food safety, warehouse/logistics safety, and EHS roles
  unless the role has explicit cybersecurity responsibility.
- Description completeness: if the list API lacks requirements, fetch or parse
  the detail API/page. Store both responsibility and requirement sections when
  the official source exposes them.
- Detail robustness: transient network/detail failures may degrade one job to
  list data, but must not silently lose requirements for every job. Add retry
  tests for source-specific unstable endpoints. For high-volume sources, use
  bounded concurrency for detail enrichment.
- Multi-location fields: preserve all official locations but split and normalize
  them for filters. `北京`, `北京市`, `Beijing`, and district-only Beijing
  values must collapse to one `北京` filter value.
- Regional coverage: when a user reports a missing city, verify three facts
  separately: whether the database has active target jobs for the city, whether
  official raw search returns non-target city jobs, and whether adapter
  pagination caps could have skipped target jobs.
- Pagination completeness: if an official API reports a total larger than the
  configured full-scan page cap, the adapter must switch to a bounded
  target-keyword pagination strategy and deduplicate across keyword queries.
  Do not activate a source whose live smoke only proves the first page works.
- Collection performance: long-running sources must have explicit per-source
  progress output, bounded per-keyword pagination, and transport retries for
  known flaky endpoints. A single large source must not make `collect --live`
  look hung with no visible source name.
- Job type fields: normalize vendor codes such as `J0012` before storage/filter
  display. Raw code-only values must not become UI filter options.
- Stable identity: every job must have a stable external id or official URL so
  repeated collection updates the existing row instead of duplicating it.
- Offline tests: each adapter must have fixture tests that do not require
  network I/O.
- Live smoke: before activation, run a small live smoke and record source URL,
  API method/payload, count, salary behavior, and any limitations.

## Per-Company Deliverables

Each parallel task should produce:

- `src/findjobs/adapters/<company>.py`
- `tests/fixtures/adapters/<company>.json`
- Focused tests in `tests/test_phase3.py` or a dedicated adapter test file.
- A short note with:
  - official careers URL,
  - verified API endpoint or parsing strategy,
  - request method and payload,
  - observed live smoke result,
  - salary disclosure behavior.

## Completed Parallel Tasks

### Kuaishou

- Company slug: `kuaishou`
- Careers URL: `https://zhaopin.kuaishou.cn`
- Target keywords: shared `TARGET_KEYWORDS`
- Adapter: `kuaishou_official`
- Notes: signed official API, no login cookie required, salary not disclosed.

### Meituan

- Company slug: `meituan`
- Careers URL: `https://zhaopin.meituan.com`
- Target keywords: shared `TARGET_KEYWORDS`
- Adapter: `meituan_official`
- Notes: official JSON API, no login cookie required, salary not disclosed.
  Detail enrichment uses bounded concurrency so requirement text is preserved
  without making high-volume collection impractically slow.

### JD

- Company slug: `jd`
- Careers URL: `https://zhaopin.jd.com`
- Target keywords: shared `TARGET_KEYWORDS`
- Adapter: `jd_official`
- Notes: official form API plus `job_count`, no login cookie required, salary
  not disclosed.

### DeepSeek

- Company slug: `deepseek`
- Careers URL:
  `https://app.mokahr.com/social-recruitment/high-flyer/140576?orgId=high-flyer`
- Adapter: `deepseek_moka`
- Notes: official DeepSeek homepage links to this Moka site. The Moka list API
  returns AES-CBC encrypted JSON; adapter decrypts it with the page `aesIv` and
  response `necromancer` key. Collection uses shared `TARGET_KEYWORDS` plus
  the DeepSeek-specific `AGI` term only; generic `平台` is intentionally not a
  search keyword. Salary is not disclosed.

### ByteDance

- Company slug: `bytedance`
- Careers URL: `https://jobs.bytedance.com`
- Adapter: `bytedance_official`, delegating live collection to the standard
  Feishu official adapter while preserving ByteDance fixture parsing tests.
- Live smoke: standard Feishu API reported `total=10000`, which exceeds the
  blank full-scan cap. The shared Feishu adapter now switches large sources to
  AI/Security target-keyword pagination and deduplicates across keywords. After
  the fix, the 2026-07-01 live collection returned 1595 target jobs for the
  run and 1613 active ByteDance jobs after accumulated updates.
- Notes: activated only after confirming the source is the official careers
  domain and no Huawei source is introduced.

### Xiaomi

- Company slug: `xiaomi`
- Careers URL: `https://xiaomi.jobs.f.mioffice.cn`
- Adapter: `feishu_official`
- Live smoke: Feishu API reported `total=1916`, which exceeds the blank
  full-scan cap. The shared Feishu adapter now uses large-source keyword
  pagination for Xiaomi as well; live collection returned 91 target jobs after
  the fix. Salary is not disclosed.

### Z.ai / Zhipu

- Company slug: `zhipu`
- Careers URL: `https://zhipu-ai.jobs.feishu.cn`
- Adapter: `feishu_official`
- Live smoke: Feishu API returned official Z.ai/Zhipu jobs; classification
  filtering removed sales, ecosystem, content, design, community, and intern
  noise. Salary is not disclosed.

### SenseTime

- Company slug: `sensetime`
- Careers URL: `https://sensetime.jobs.feishu.cn`
- Adapter: `feishu_official`
- Live smoke: raw 47 jobs returned by the standard feishu_official adapter.
  4 domain-relevant (AI/Security) after standard classification filtering.
  Salary is not disclosed — the Feishu ATS does not expose salary fields in its
  standard job listing API response.
- Activation: `config/sources.yaml` updated with `is_active: true`,
  `fetch_url: ""`, adapter `feishu_official`.

### iFlytek

- Company slug: `iflytek`
- Careers URL: `https://iflytek.zhiye.com` (BeiSen ATS portal)
- Target endpoint: `https://iflytek.zhiye.com/api/Jobad/GetJobAdPageList`
- Request: POST JSON with `PageIndex`, `PageSize`, `Category: ["1"]`,
  `KeyWords`, browser-like headers including `X-Requested-With: XMLHttpRequest`
  and `langType: zh_CN`.
- Adapter: `iflytek_official`
- Live smoke (2026-07-01): /api/Jobad/GetJobAdPageList endpoint confirmed
  reachable.  Keyword `安全` returned Count=44.  First result title was
  `安全解决方案工程师`.  Salary field is null in the API response; Duty and
  Require fields are both present.
- Salary: not disclosed by the API (field null/empty).
- Activation: `config/sources.yaml` updated with `is_active: true`,
  `fetch_url` set to the endpoint, adapter `iflytek_official`.

## 2026-07-01 Full Live Collection Evidence

- `uv run findjobs collect --live` completed all 17 active sources.
- Final active jobs after prune: 2789 across 17 companies.
- Guardrail audit: empty tags 0, algorithm title/type residual 0, Huawei
  sources 0, raw `J00*` type values 0.
- Regional audit: active Xi'an jobs 29, so the previous zero-Xi'an result was
  a collection coverage issue rather than confirmed official-source absence.
- Description audit: Tencent active jobs with requirement marker 303; Meituan
  active jobs with requirement marker 109.
- Weekly export refreshed with `uv run findjobs weekly --no-live`; exported
  `reports/weekly/jobs.jsonl` contains 2789 job facts and
  `reports/weekly/ai-security.jsonl` contains 112 AI Security jobs.

## 2026-07-07 Alibaba / Ant Official Adapter Evidence

- Ant Group:
  - Official page: `https://talent.antgroup.com/off-campus`.
  - Verified API: `POST https://hrcareersweb.antgroup.com/api/social/position/search?ctoken=...`.
  - Response shape: `content` list plus `totalCount`; list items include
    `id`, `name`, `categories`, `publishTime`, `workLocations`,
    `description`, and `requirement`.
  - Live smoke: blank search returned `totalCount=951`.
  - Activation: `antgroup-talent` now uses `antgroup_official`.
- Alibaba:
  - Central page `https://talent.alibaba.com` is an official directory, not a
    stable job-list API.
  - Verified official business sub-site APIs use the same XSRF pattern:
    GET `/off-campus/position-list?lang=zh` for `XSRF-TOKEN`, then POST
    `/position/search?_csrf=<token>`.
  - Activated sub-sites: `https://careers.aliyun.com`,
    `https://careers-tongyi.alibaba.com`, `https://talent.quark.cn`,
    `https://talent.dingtalk.com`, and
    `https://talent-holding.alibaba.com`.
  - Live smoke totals observed: Aliyun 674, Tongyi 73, Quark 332, DingTalk
    103, Holding 532.
  - Adapter behavior: blank scan is used first; if a sub-site reports a higher
    `totalCount` than blank pages return (Aliyun currently caps blank results
    at 500), the adapter supplements with shared AI/Security target keywords.
  - Activation: the sub-sites use `alibaba_group_official`; central
    `alibaba-talent` remains inactive as a directory marker.
  - Targeted collection after activation:
    - Ant Group: 140 persisted target jobs from 946 unique official rows.
    - Aliyun: 172 persisted target jobs from 566 unique rows after blank-cap
      keyword supplement.
    - Tongyi: 18 persisted target jobs from 73 official rows.
    - Quark: 27 persisted target jobs from 330 official rows.
    - DingTalk: 20 persisted target jobs from 103 official rows.
    - Alibaba Holding: 172 persisted target jobs from 532 official rows.
  - Reclassification/prune after the Security filter update scanned 3081 rows,
    updated 1 row, and deleted 168 now-irrelevant jobs.

## Inactive Official-Source Backlog

These sources remain tracked in `config/sources.yaml` but inactive. Do not
enable them until a stable, public, official-source job list endpoint or parser
is verified with live smoke evidence.

### Alibaba

- Company slug: `alibaba`
- Candidate careers URL: `https://talent.alibaba.com`
- Current status: kept inactive because the central page is an official
  directory. Stable job collection is handled by the verified Alibaba business
  sub-site sources above.

## Codex Integration Gate

After each task finishes, Codex verifies:

- adapter tests pass offline;
- live smoke returns official-source data;
- parsed jobs have stable external IDs or URLs;
- salary is `未披露` unless official response explicitly discloses salary;
- algorithm-title or algorithm-type jobs do not get `AI` or `AI Security`;
- only then update `config/sources.yaml` and adapter registration.
