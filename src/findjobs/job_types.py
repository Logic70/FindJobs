"""Job type splitting and normalization for storage and UI filters."""

from __future__ import annotations

import re


_SEPARATOR_RE = re.compile(r"\s*(?:/|、|,|，|;|；|\n|\r)\s*")

_CODE_ALIASES = {
    "J0012": "技术",
    "J0011": "技术",
    "J0005": "产品",
    "J0004": "运营",
    "J0002": "职能",
    "J0014": "用户研究",
}

_EXACT_ALIASES = {
    "技术类": "技术",
    "软件": "技术",
    "软件类": "技术",
    "研发类": "技术",
    "后端": "技术",
    "前端": "技术",
    "客户端": "技术",
    "服务端": "技术",
    "大数据": "技术",
    "数据开发": "技术",
    "基础架构": "技术",
    "云计算": "技术",
    "IT支持": "技术",
    "互联网": "技术",
    "电子": "技术",
    "网游": "技术",
    "运维": "技术",
    "运维类": "技术",
    "硬件": "技术",
    "硬件类": "技术",
    "人工智能": "AI工程",
    "AI核心系统研发": "AI工程",
    "机器学习": "AI工程",
    "深度学习": "AI工程",
    "大模型": "AI工程",
    "产品类": "产品",
    "产品部门": "产品",
    "模型数据策略": "产品",
    "策划": "产品",
    "项目": "产品",
    "运营类": "运营",
    "用户运营": "运营",
    "业务运营": "运营",
    "职能支持": "职能",
    "公司事务": "职能",
    "财务": "职能",
    "法律与公共策略": "职能",
    "综合支持": "职能",
    "支持": "职能",
    "综合": "职能",
    "采购": "职能",
    "战略": "职能",
    "战略与投资": "职能",
    "商业分析": "职能",
    "商业分析类": "职能",
    "数据分析": "职能",
    "内审": "合规",
    "物流": "其他",
    "零售类": "其他",
    "其它": "其他",
    "其他": "其他",
    "full-time": "全职",
    "fulltime": "全职",
    "contract": "合同",
    "intern": "实习",
    "internship": "实习",
}

_SUBSTRING_ALIASES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"J\d{4}", re.I), "未分类"),
    (re.compile(r"算法"), "算法"),
    (re.compile(r"AI|人工智能|大模型|模型|智能|机器学习|深度学习", re.I), "AI工程"),
    (re.compile(r"研发|开发|软件|技术|工程|架构|运维|硬件|测试|后端|前端|客户端|服务端|大数据|数据开发|基础架构|云计算|IT支持|多媒体"), "技术"),
    (re.compile(r"产品|策略"), "产品"),
    (re.compile(r"运营|治理|审核"), "运营"),
    (re.compile(r"安全"), "安全"),
    (re.compile(r"风控|风险控制|反作弊"), "风控"),
    (re.compile(r"合规|内审"), "合规"),
    (re.compile(r"金融|资金"), "金融"),
    (re.compile(r"职能|公司事务|财务|法务|法律|人力|招聘|组织|综合支持|综合|支持|采购|战略|商业分析|数据分析"), "职能"),
    (re.compile(r"政企|企业服务"), "企业服务"),
    (re.compile(r"电商"), "电商"),
    (re.compile(r"游戏"), "游戏"),
]

_JOB_TYPE_ORDER = {
    "AI工程": 10,
    "安全": 20,
    "技术": 30,
    "产品": 40,
    "运营": 50,
    "风控": 60,
    "合规": 70,
    "金融": 80,
    "企业服务": 90,
    "电商": 100,
    "游戏": 110,
    "用户研究": 120,
    "职能": 130,
    "全职": 900,
    "实习": 910,
    "合同": 920,
    "其他": 990,
    "未分类": 999,
}


def normalize_job_type(value: str) -> str:
    """Normalize one job-type segment to a stable filter value."""
    text = re.sub(r"\s+", "", (value or "").strip())
    if not text:
        return ""
    code_alias = _CODE_ALIASES.get(text.upper())
    if code_alias:
        return code_alias
    exact_alias = _EXACT_ALIASES.get(text) or _EXACT_ALIASES.get(text.lower())
    if exact_alias:
        return exact_alias
    for pattern, normalized in _SUBSTRING_ALIASES:
        if pattern.search(text):
            return normalized
    return text


def split_job_types(value: str) -> list[str]:
    """Split a possibly multi-valued job type into normalized values."""
    results: list[str] = []
    for part in _SEPARATOR_RE.split(value or ""):
        normalized = normalize_job_type(part)
        if normalized and normalized not in results:
            results.append(normalized)
    return results


def format_job_type(value: str) -> str:
    """Return a stable display/storage representation for job-type values."""
    job_types = split_job_types(value)
    job_types.sort(key=lambda item: (_JOB_TYPE_ORDER.get(item, 500), item))
    return "、".join(job_types)


def job_type_matches(raw_job_type: str, selected_job_type: str) -> bool:
    """Return True when a raw multi-valued job type contains the selected type."""
    selected = normalize_job_type(selected_job_type)
    return bool(selected) and selected in split_job_types(raw_job_type)
