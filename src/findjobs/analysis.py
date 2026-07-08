"""Offline analysis workflow over exported FindJobs facts.

This module intentionally consumes exported JSONL rows instead of querying the
database. It provides a deterministic local workflow, while external AI tools
remain optional consumers of the same exported facts.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from findjobs.locations import split_locations


@dataclass(frozen=True)
class WeeklyAnalysisResult:
    """Paths and counts produced by the weekly analysis workflow."""

    summary_path: Path
    ai_security_path: Path
    manifest_path: Path
    profile_needed_path: Path | None
    matches_path: Path | None
    priorities_path: Path | None
    total_jobs: int
    ai_security_jobs: int
    career_advice_path: Path | None = None


@dataclass(frozen=True)
class ProfileFacts:
    """Small deterministic profile model parsed from profile/profile.md."""

    raw_text: str
    target_cities: list[str]
    excluded_companies: list[str]
    minimum_salary: float | None
    keywords: list[str]


@dataclass(frozen=True)
class MatchResult:
    """A scored match row derived only from profile text and exported facts."""

    row: dict[str, Any]
    score: int
    tier: str
    reasons: list[str]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load exported job facts from a JSONL file."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: expected object")
            rows.append(row)
    return rows


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    """Write rows as UTF-8 JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def _tags(row: dict[str, Any]) -> list[str]:
    value = row.get("matched_tags") or []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _company(row: dict[str, Any]) -> str:
    return str(row.get("company_slug") or row.get("company_name") or "unknown")


def _fmt_counter(counter: Counter[str], limit: int | None = None) -> list[str]:
    items = counter.most_common(limit)
    if not items:
        return ["- 无"]
    return [f"- {key}: {value}" for key, value in items]


def _has_tag(row: dict[str, Any], tag: str) -> bool:
    return tag in _tags(row)


def parse_profile(path: Path) -> ProfileFacts:
    """Parse a lightweight markdown profile for deterministic matching."""
    text = path.read_text(encoding="utf-8")
    lower = text.lower()

    city_aliases = {
        "beijing": "北京",
        "北京": "北京",
        "shanghai": "上海",
        "上海": "上海",
        "shenzhen": "深圳",
        "深圳": "深圳",
        "hangzhou": "杭州",
        "杭州": "杭州",
        "guangzhou": "广州",
        "广州": "广州",
        "chengdu": "成都",
        "成都": "成都",
        "remote": "remote",
        "远程": "remote",
    }
    target_cities: list[str] = []
    for needle, canonical in city_aliases.items():
        if needle in lower or needle in text:
            if canonical not in target_cities:
                target_cities.append(canonical)

    def _section_text(section_name: str) -> str:
        lines = text.splitlines()
        capture = False
        selected: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                heading = stripped.lstrip("#").strip().lower()
                if capture and heading != section_name.lower():
                    break
                capture = heading == section_name.lower()
                continue
            if capture:
                selected.append(line)
        return "\n".join(selected)

    excluded_text = _section_text("Excluded Companies")
    excluded_lower = excluded_text.lower()
    excluded_companies: list[str] = []
    for company in (
        "huawei",
        "华为",
        "tencent",
        "腾讯",
        "baidu",
        "百度",
        "meituan",
        "美团",
        "jd",
        "京东",
        "kuaishou",
        "快手",
        "netease",
        "网易",
    ):
        if company in excluded_lower or company in excluded_text:
            excluded_companies.append(company)

    minimum_salary = None
    import re

    minimum_match = re.search(r"minimum\D+(\d+)", lower)
    if minimum_match:
        minimum_salary = float(minimum_match.group(1))

    keyword_map = {
        "llm": ["llm", "大模型"],
        "agent": ["agent", "智能体"],
        "mlops": ["mlops"],
        "appsec": ["appsec", "应用安全"],
        "red teaming": ["red teaming", "红队"],
        "ai security": ["ai安全", "ai security"],
        "risk": ["risk", "风控"],
        "data security": ["data security", "数据安全"],
        "cloud security": ["cloud security", "云安全"],
    }
    keywords: list[str] = []
    for profile_word, row_words in keyword_map.items():
        if profile_word in lower or any(word in text for word in row_words):
            keywords.extend(row_words)

    return ProfileFacts(
        raw_text=text,
        target_cities=target_cities,
        excluded_companies=excluded_companies,
        minimum_salary=minimum_salary,
        keywords=list(dict.fromkeys(keywords)),
    )


def _algorithm_ai_bad(rows: list[dict[str, Any]]) -> int:
    total = 0
    for row in rows:
        title = str(row.get("title") or "")
        job_type = str(row.get("job_type") or "")
        if "算法" not in title and "算法" not in job_type:
            continue
        if _has_tag(row, "AI") or _has_tag(row, "AI Security"):
            total += 1
    return total


def _date_range(rows: list[dict[str, Any]]) -> tuple[str, str]:
    values = [
        str(row.get("last_seen_at") or row.get("first_seen_at") or "")
        for row in rows
        if row.get("last_seen_at") or row.get("first_seen_at")
    ]
    if not values:
        return ("未知", "未知")
    return (min(values), max(values))


def _examples(rows: list[dict[str, Any]], limit: int = 10) -> list[str]:
    preferred: list[str] = []
    fallback: list[str] = []
    for row in rows:
        line = (
            f"- {_company(row)} | {row.get('title') or ''} | "
            f"{row.get('location') or '未标注'} | "
            f"{row.get('job_type') or '未标注'} | "
            f"{'未披露' if not row.get('salary_disclosed') else row.get('salary_text')}"
        )
        title = str(row.get("title") or "")
        if any(
            keyword in title
            for keyword in ("安全", "AI", "智能体", "AGI", "大模型", "Agent", "LLM")
        ):
            preferred.append(line)
        else:
            fallback.append(line)
    return (preferred + fallback)[:limit] or ["- 无"]


def _row_search_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("company_slug", "company_name", "title", "location", "job_type")
    ).lower()


def score_job(row: dict[str, Any], profile: ProfileFacts) -> MatchResult:
    """Score one exported job against parsed profile facts."""
    tags = _tags(row)
    search_text = _row_search_text(row)
    reasons: list[str] = []
    score = 0

    if any(company in search_text for company in profile.excluded_companies):
        return MatchResult(row=row, score=0, tier="Excluded", reasons=["命中排除公司"])

    if "AI Security" in tags:
        score += 40
        reasons.append("同时匹配 AI 与安全标签")
    elif "AI" in tags:
        score += 25
        reasons.append("匹配 AI 标签")
    elif "Security" in tags:
        score += 25
        reasons.append("匹配安全标签")

    if profile.target_cities:
        location = str(row.get("location") or "").lower()
        city_hit = False
        for city in profile.target_cities:
            if city == "remote" and ("remote" in location or "远程" in location):
                city_hit = True
            elif city != "remote" and city in str(row.get("location") or ""):
                city_hit = True
        if city_hit:
            score += 20
            reasons.append("地域匹配")
        else:
            reasons.append("地域未明确匹配")

    keyword_hits = []
    for keyword in profile.keywords:
        if keyword.lower() in search_text or keyword in str(row.get("title") or ""):
            keyword_hits.append(keyword)
    if keyword_hits:
        score += min(25, 5 * len(set(keyword_hits)))
        reasons.append("关键词匹配: " + ", ".join(sorted(set(keyword_hits))[:5]))

    if row.get("salary_disclosed") is True:
        salary_min = row.get("salary_min")
        if profile.minimum_salary is None or (
            salary_min is not None and float(salary_min) >= profile.minimum_salary
        ):
            score += 10
            reasons.append("薪资披露且不低于最低期望")
        else:
            score -= 10
            reasons.append("薪资披露但低于最低期望")
    else:
        reasons.append("薪资未披露，未参与评分")

    score = max(0, min(100, score))
    if score >= 75:
        tier = "Top priority"
    elif score >= 50:
        tier = "Good fit"
    else:
        tier = "Low priority"
    return MatchResult(row=row, score=score, tier=tier, reasons=reasons)


def build_matches(rows: list[dict[str, Any]], profile: ProfileFacts) -> list[MatchResult]:
    """Score and sort exported jobs by profile match."""
    matches = [score_job(row, profile) for row in rows]
    return sorted(matches, key=lambda item: item.score, reverse=True)


def _salary_text(row: dict[str, Any]) -> str:
    if not row.get("salary_disclosed"):
        return "未披露"
    return str(row.get("salary_text") or "")


def render_matches(matches: list[MatchResult], run_date: str) -> str:
    """Render the match report markdown."""
    lines = [
        f"# {run_date} FindJobs 个人匹配分析",
        "",
        "评分只使用导出岗位事实和 `profile/profile.md`，不估算薪资。",
        "",
        "| Score | Tier | Company | Title | Location | Salary | Tags | Reasons |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for item in matches[:100]:
        row = item.row
        lines.append(
            "| {score} | {tier} | {company} | {title} | {location} | {salary} | "
            "{tags} | {reasons} |".format(
                score=item.score,
                tier=item.tier,
                company=_company(row),
                title=str(row.get("title") or "").replace("|", "/"),
                location=str(row.get("location") or "未标注").replace("|", "/"),
                salary=_salary_text(row).replace("|", "/"),
                tags=", ".join(_tags(row)),
                reasons="; ".join(item.reasons).replace("|", "/"),
            )
        )

    buckets = Counter(item.tier for item in matches)
    lines.extend(
        [
            "",
            "## 汇总",
            f"- Top priority: {buckets['Top priority']}",
            f"- Good fit: {buckets['Good fit']}",
            f"- Low priority: {buckets['Low priority']}",
            f"- Excluded: {buckets['Excluded']}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_priorities(matches: list[MatchResult], run_date: str) -> str:
    """Render a tiered priority report from match results."""
    lines = [
        f"# {run_date} FindJobs 投递优先级",
        "",
        "排序依据：匹配分数、AI Security 标签、地域匹配、薪资披露情况。",
    ]
    for tier in ("Top priority", "Good fit", "Low priority"):
        tier_rows = [item for item in matches if item.tier == tier][:30]
        lines.extend(["", f"## {tier}", ""])
        if not tier_rows:
            lines.append("- 无")
            continue
        lines.extend(
            [
                "| Score | Company | Title | Location | Salary | URL |",
                "|---:|---|---|---|---|---|",
            ]
        )
        for item in tier_rows:
            row = item.row
            lines.append(
                "| {score} | {company} | {title} | {location} | {salary} | {url} |".format(
                    score=item.score,
                    company=_company(row),
                    title=str(row.get("title") or "").replace("|", "/"),
                    location=str(row.get("location") or "未标注").replace("|", "/"),
                    salary=_salary_text(row).replace("|", "/"),
                    url=str(row.get("url") or "").replace("|", "/"),
                )
            )
    return "\n".join(lines) + "\n"


def _profile_has_any(profile: ProfileFacts, words: tuple[str, ...]) -> bool:
    text = profile.raw_text.lower()
    return any(word.lower() in text or word in profile.raw_text for word in words)


def render_career_advice(
    matches: list[MatchResult],
    profile: ProfileFacts,
    run_date: str,
) -> str:
    """Render deterministic career and learning advice from exported facts."""
    visible_matches = [item for item in matches if item.tier != "Excluded"]
    top_matches = visible_matches[:10]
    top_rows = visible_matches[:50]

    tag_counts: Counter[str] = Counter()
    title_text = ""
    for item in top_rows:
        tag_counts.update(_tags(item.row))
        title_text += " " + str(item.row.get("title") or "")

    skill_signals = [
        (
            "AI 安全 / 大模型安全",
            ("AI Security", "AI安全", "大模型安全", "LLM security", "红队"),
        ),
        ("应用安全 / SDL", ("AppSec", "应用安全", "SDL", "漏洞", "渗透")),
        ("MLOps / 推理部署", ("MLOps", "推理", "模型部署", "inference")),
        ("智能体 / RAG 工程", ("Agent", "智能体", "RAG", "LangChain")),
        ("云安全 / 数据安全", ("云安全", "数据安全", "隐私", "Cloud Security")),
        ("风控 / 反作弊", ("风控", "反作弊", "反欺诈", "risk", "fraud")),
        ("模型微调 / 评测", ("微调", "fine-tuning", "评测", "evaluation")),
    ]
    observed_signals: list[str] = []
    learning_gaps: list[str] = []
    search_surface = title_text + " " + " ".join(tag_counts)
    for label, words in skill_signals:
        if any(word.lower() in search_surface.lower() or word in search_surface for word in words):
            observed_signals.append(label)
            if not _profile_has_any(profile, words):
                learning_gaps.append(label)

    lines = [
        f"# {run_date} FindJobs 发展与学习建议",
        "",
        "本报告只使用导出岗位事实和 `profile/profile.md`，不读取或写入数据库，"
        "不估算未披露薪资，不补写官网未采集到的岗位要求。",
        "",
        "## 推荐岗位方向",
    ]

    if not top_matches:
        lines.append("- 暂无可推荐岗位；请先检查导出数据和 profile。")
    else:
        for item in top_matches:
            row = item.row
            lines.append(
                "- {company} | {title} | {location} | {salary} | {tier} {score}: {reasons}".format(
                    company=_company(row),
                    title=str(row.get("title") or "").replace("|", "/"),
                    location=str(row.get("location") or "未标注").replace("|", "/"),
                    salary=_salary_text(row).replace("|", "/"),
                    tier=item.tier,
                    score=item.score,
                    reasons="; ".join(item.reasons).replace("|", "/"),
                )
            )

    lines.extend(["", "## 发展建议"])
    if tag_counts["AI Security"]:
        lines.append(
            "- 优先走 AI 安全交叉方向：当前高匹配岗位中出现 AI Security 标签，"
            "更适合把安全工程经验和大模型/智能体落地能力绑定展示。"
        )
    elif tag_counts["Security"] >= tag_counts["AI"]:
        lines.append(
            "- 优先走安全工程方向：当前高匹配岗位更偏安全、风控、隐私或应用安全。"
        )
    else:
        lines.append(
            "- 优先走 AI 工程方向：当前高匹配岗位更偏 AI 平台、应用工程、推理或 MLOps。"
        )

    if profile.target_cities:
        lines.append("- 地域策略：优先筛选 " + "、".join(profile.target_cities) + " 的岗位。")
    else:
        lines.append("- 地域策略：profile 未明确目标城市，建议先补充可接受城市。")

    if observed_signals:
        lines.append("- 当前导出岗位中反复出现的方向：" + "、".join(observed_signals[:6]) + "。")

    lines.extend(["", "## 学习建议"])
    if learning_gaps:
        for gap in learning_gaps[:6]:
            lines.append(f"- 补强 {gap}：这是当前推荐岗位标题/标签中出现、但 profile 未明显覆盖的方向。")
    else:
        lines.append("- 继续深化 profile 已覆盖的核心方向，并用项目经历证明可落地能力。")

    lines.extend(
        [
            "",
            "## 事实边界",
            "- 以上建议不是新增岗位事实，只是基于导出岗位标题、标签、地域、薪资披露状态和 profile 的分析。",
            "- 薪资未披露时统一按未知处理。",
            "- 岗位要求缺失时不会推断要求；需要回到官网链接核对。",
        ]
    )
    return "\n".join(lines) + "\n"


def render_weekly_summary(rows: list[dict[str, Any]], run_date: str) -> str:
    """Render a Chinese weekly factual summary from exported rows."""
    company_counts = Counter(_company(row) for row in rows)
    location_counts: Counter[str] = Counter()
    for row in rows:
        raw_location = str(row.get("location") or "")
        location_counts.update(split_locations(raw_location) or ["未标注"])
    job_type_counts = Counter(str(row.get("job_type") or "未标注") for row in rows)
    tag_counts: Counter[str] = Counter()
    for row in rows:
        tag_counts.update(_tags(row))

    ai_security_rows = [row for row in rows if _has_tag(row, "AI Security")]
    ai_security_by_company = Counter(_company(row) for row in ai_security_rows)
    salary_disclosed = sum(1 for row in rows if row.get("salary_disclosed") is True)
    start, end = _date_range(rows)

    lines: list[str] = [
        f"# {run_date} FindJobs 周报",
        "",
        "## 概览",
        f"- 本次导出岗位总数：{len(rows)} 个数据库唯一岗位。",
        f"- 数据时间范围：{start} 至 {end}。",
        f"- 薪资明确披露：{salary_disclosed} 个；未披露：{len(rows) - salary_disclosed} 个。",
        f"- 标题或岗位类型包含“算法”但仍打 AI 的数量：{_algorithm_ai_bad(rows)}。",
        "",
        "## 公司分布",
        *_fmt_counter(company_counts),
        "",
        "## 标签分布",
        *_fmt_counter(tag_counts),
        "",
        "## AI Security 按公司分布",
        *_fmt_counter(ai_security_by_company),
        "",
        "## 地域 Top 10",
        *_fmt_counter(location_counts, limit=10),
        "",
        "## 岗位类型 Top 10",
        *_fmt_counter(job_type_counts, limit=10),
        "",
        "## 事实样例",
        "以下仅为官网采集事实样例，不代表个人匹配优先级：",
        "",
        *_examples(ai_security_rows),
        "",
        "## 数据限制",
        "- 只使用导出的官网采集事实，不采集第三方招聘平台。",
        "- 未披露薪资不估算。",
        "- 个人画像缺失时，不做个人匹配分数或投递优先级。",
    ]
    return "\n".join(lines) + "\n"


def render_profile_needed(
    rows: list[dict[str, Any]], ai_security_rows: list[dict[str, Any]]
) -> str:
    """Render the match-analysis blocked report when profile/profile.md is absent."""
    companies = Counter(_company(row) for row in rows)
    company_text = ", ".join(f"{k} {v}" for k, v in companies.most_common())
    return (
        "# 个人匹配分析未执行\n\n"
        "原因：`profile/profile.md` 不存在。为了避免编造个人背景、城市偏好、"
        "薪资期望或投递优先级，系统没有生成匹配分数。\n\n"
        "## 当前可用岗位事实\n"
        f"- 导出岗位总数：{len(rows)}\n"
        f"- AI Security 岗位数：{len(ai_security_rows)}\n"
        f"- 公司分布：{company_text}\n"
        "- 薪资：仅使用官网披露字段；未披露时不估算。\n\n"
        "## 需要补充的画像字段\n"
        "- 目标城市或可接受城市\n"
        "- 目标岗位方向\n"
        "- AI 工程 / AI 安全 / 应用安全 / 风控等优先级\n"
        "- 当前技能栈和项目经历\n"
        "- 薪资期望和可接受下限\n"
        "- 排除公司或行业限制\n"
    )


def run_weekly_analysis(
    *,
    jobs_path: Path,
    reports_dir: Path,
    run_date: str | None = None,
    profile_path: Path | None = None,
) -> WeeklyAnalysisResult:
    """Run the deterministic weekly analysis workflow over exported jobs."""
    rows = load_jsonl(jobs_path)
    actual_date = run_date or date.today().isoformat()
    profile = profile_path or Path("profile") / "profile.md"

    weekly_dir = reports_dir / "weekly"
    match_dir = reports_dir / "match"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    match_dir.mkdir(parents=True, exist_ok=True)

    ai_security_rows = [row for row in rows if _has_tag(row, "AI Security")]
    summary_path = weekly_dir / f"{actual_date}-summary.md"
    ai_security_path = weekly_dir / "ai-security.jsonl"
    manifest_path = weekly_dir / f"{actual_date}-analysis-manifest.json"
    profile_needed_path: Path | None = None
    matches_path: Path | None = None
    priorities_path: Path | None = None
    career_advice_path: Path | None = None

    summary_path.write_text(
        render_weekly_summary(rows, actual_date),
        encoding="utf-8",
    )
    write_jsonl(ai_security_rows, ai_security_path)

    if not profile.exists():
        profile_needed_path = match_dir / f"{actual_date}-profile-needed.md"
        profile_needed_path.write_text(
            render_profile_needed(rows, ai_security_rows),
            encoding="utf-8",
        )
    else:
        priority_dir = reports_dir / "priority"
        priority_dir.mkdir(parents=True, exist_ok=True)
        profile_facts = parse_profile(profile)
        matches = build_matches(rows, profile_facts)
        matches_path = match_dir / f"{actual_date}-matches.md"
        priorities_path = priority_dir / f"{actual_date}-priorities.md"
        career_advice_path = match_dir / f"{actual_date}-career-advice.md"
        matches_path.write_text(
            render_matches(matches, actual_date),
            encoding="utf-8",
        )
        priorities_path.write_text(
            render_priorities(matches, actual_date),
            encoding="utf-8",
        )
        career_advice_path.write_text(
            render_career_advice(matches, profile_facts, actual_date),
            encoding="utf-8",
        )

    manifest = {
        "jobs_path": str(jobs_path),
        "summary_path": str(summary_path),
        "ai_security_path": str(ai_security_path),
        "profile_path": str(profile),
        "profile_needed_path": str(profile_needed_path)
        if profile_needed_path is not None
        else None,
        "matches_path": str(matches_path) if matches_path is not None else None,
        "priorities_path": str(priorities_path)
        if priorities_path is not None
        else None,
        "career_advice_path": str(career_advice_path)
        if career_advice_path is not None
        else None,
        "total_jobs": len(rows),
        "ai_security_jobs": len(ai_security_rows),
        "algorithm_ai_bad": _algorithm_ai_bad(rows),
        "guardrails": [
            "exported facts only",
            "do not estimate undisclosed salary",
            "do not write to database",
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return WeeklyAnalysisResult(
        summary_path=summary_path,
        ai_security_path=ai_security_path,
        manifest_path=manifest_path,
        profile_needed_path=profile_needed_path,
        matches_path=matches_path,
        priorities_path=priorities_path,
        total_jobs=len(rows),
        ai_security_jobs=len(ai_security_rows),
        career_advice_path=career_advice_path,
    )
