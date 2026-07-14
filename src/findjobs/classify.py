"""Job title/description classifier for AI and cybersecurity roles.

Provides two entry points:

* :func:`classify_job` -- legacy function returning ``list[str]`` of tags.
* :func:`classify_job_detailed` -- new function returning a frozen
  :class:`DetailedClassification` with tags, relevance status, machine-readable
  reason codes, and the classification version.

Version
-------
``CLASSIFICATION_VERSION = "2.1.1"`` -- increment whenever the contract
(reason codes, status semantics, or pattern logic) changes in a way that
stale persisted data would not reflect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Version & result type
# ---------------------------------------------------------------------------

CLASSIFICATION_VERSION = "2.1.1"


@dataclass(frozen=True)
class DetailedClassification:
    """Frozen result of a detailed job classification.

    Attributes:
        tags:             Tuple of domain tags (e.g. ``("AI", "Security")``).
        relevance_status: One of ``"target"``, ``"review"``, or ``"excluded"``.
        reasons:          Tuple of stable machine-readable reason codes.
        version:          The ``CLASSIFICATION_VERSION`` used.
    """

    tags: tuple[str, ...] = ()
    relevance_status: str = "excluded"
    reasons: tuple[str, ...] = ()
    version: str = CLASSIFICATION_VERSION


# ---------------------------------------------------------------------------
# Machine-readable reason codes  (stable — never rename or re-purpose)
# ---------------------------------------------------------------------------

# Excluded reasons
REASON_ALGORITHM = "algorithm_in_title_or_type"
REASON_PRODUCT = "product_manager_or_operations"
REASON_BUSINESS_OPS = "business_strategy_risk_operations"
REASON_ANALYSIS = "data_business_operations_analysis"
REASON_PLANNING = "planning_creative_planning"
REASON_SALES = "sales_marketing_customer_success"
REASON_PROJECT = "project_management"
REASON_INTERN = "generic_internship"
REASON_QA_DESIGN_LEGAL = "qa_audit_content_design_legal_admin"
REASON_NO_SIGNALS = "no_target_signals"

# Target reasons
REASON_AI_SURFACE = "ai_surface_signals"
REASON_SECURITY_SURFACE = "security_surface_signals"
REASON_AI_SECURITY_SURFACE = "ai_and_security_surface_signals"

# Review reasons
REASON_REVIEW_AI = "engineering_title_with_ai_responsibilities"
REASON_REVIEW_SECURITY = "engineering_title_with_security_responsibilities"
REASON_REVIEW_AI_SECURITY = "engineering_title_with_ai_and_security_responsibilities"
REASON_FUNCTIONAL_SECURITY_REVIEW = "functional_security_role_requires_review"
REASON_AMBIGUOUS_AI_REVIEW = "non_engineering_ai_role_requires_review"
REASON_NON_TARGET_INFRASTRUCTURE = "generic_dba_or_vehicle_architecture"
REASON_ALGORITHM_ADJACENT_AI_REVIEW = "algorithm_adjacent_ai_role_requires_review"


# ---------------------------------------------------------------------------
# Surface-signal pattern groups (kept from Phase 1 with minimal additions)
# ---------------------------------------------------------------------------

_AI_SURFACE_SIGNALS: list[re.Pattern] = [
    re.compile(r"(^|[^a-z0-9])ai(?=$|[^a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])agi(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])llm(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])aigc(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])mlops(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(^|[^a-z0-9])agent(?=$|[^a-z0-9])", re.IGNORECASE),
    re.compile(r"\bagentic\b", re.IGNORECASE),
    re.compile(r"\bmachine\s+learning\b", re.IGNORECASE),
    re.compile(r"\bdeep\s+learning\b", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])nlp(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"\bcomputer\s+vision\b", re.IGNORECASE),
    re.compile(r"\bchatgpt\b", re.IGNORECASE),
    re.compile(r"\bgpt(?:\b|-)", re.IGNORECASE),
    re.compile(r"\btransformer\b", re.IGNORECASE),
    re.compile(r"\bbert(?:\b|-)", re.IGNORECASE),
    re.compile(r"\bdiffusion\b", re.IGNORECASE),
    re.compile(r"\brag\b", re.IGNORECASE),
    re.compile(r"\blangchain\b", re.IGNORECASE),
    re.compile(r"大模型"),
    re.compile(r"模型推理"),
    re.compile(r"推理部署"),
    re.compile(r"AI(开发|工程|应用|平台|产品|安全|助手)", re.IGNORECASE),
    re.compile(r"智能体"),
    re.compile(r"人工智能"),
    re.compile(r"生成式AI", re.IGNORECASE),
    re.compile(r"深度学习"),
    re.compile(r"自然语言处理"),
    re.compile(r"计算机视觉"),
]

_AI_DESCRIPTION_SIGNALS: list[re.Pattern] = [
    re.compile(r"(?<![a-z0-9])llm(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])aigc(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])mlops(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"\bmodel\s+inference\b", re.IGNORECASE),
    re.compile(r"\bai\s+(application|platform|agent|assistant|security)\b", re.IGNORECASE),
    re.compile(r"\bagent\s+(workflow|platform|security|application|engine)\b", re.IGNORECASE),
    re.compile(r"大模型(平台|应用|系统|工程|训练|推理|部署|安全|评测|服务|产品|基建)"),
    re.compile(r"(模型推理|推理部署|推理优化|推理框架)"),
    re.compile(r"智能体(平台|产品|应用|安全|研发|工程|评测|能力|工作流)"),
    re.compile(r"生成式AI", re.IGNORECASE),
    re.compile(r"AI(开发|工程|应用|平台|产品|安全|助手)", re.IGNORECASE),
]

_TARGET_AI_SURFACE_SIGNALS: list[re.Pattern] = [
    re.compile(r"\b(ai|llm|agent)\s*(engineer|developer|platform|infra|security)\b", re.IGNORECASE),
    re.compile(r"\bmlops\b", re.IGNORECASE),
    re.compile(r"(AI\s*Agent|AI\s*安全|AI\s*开发|AI\s*工程|AI\s*平台|AI\s*产品|AI\s*助手|模型推理|推理部署|MLOps)", re.IGNORECASE),
]

# --- Security surface signals: strong vs weak ---
# Strong: unambiguous cybersecurity — always classify as Security.
_STRONG_SECURITY_SURFACE_SIGNALS: list[re.Pattern] = [
    re.compile(r"\bai\s*security\b", re.IGNORECASE),
    re.compile(r"\b(llm|model)\s*security\b", re.IGNORECASE),
    re.compile(r"\bcontent\s*safety\b", re.IGNORECASE),
    re.compile(r"\bappsec\b", re.IGNORECASE),
    re.compile(r"\bsdl\b", re.IGNORECASE),
    re.compile(r"\bdevsecops\b", re.IGNORECASE),
    re.compile(r"\bvulnerability\b", re.IGNORECASE),
    re.compile(r"\bpenetration\b", re.IGNORECASE),
    re.compile(r"\bpentest\b", re.IGNORECASE),
    re.compile(r"\bred\s*team\b", re.IGNORECASE),
    re.compile(r"\bblue\s*team\b", re.IGNORECASE),
    re.compile(r"\bcloud\s*security\b", re.IGNORECASE),
    re.compile(r"\bdata\s*security\b", re.IGNORECASE),
    re.compile(r"\bwaf\b", re.IGNORECASE),
    re.compile(r"\bzero\s*trust\b", re.IGNORECASE),
    re.compile(r"\bprivacy\b", re.IGNORECASE),
    re.compile(r"\bfraud\b", re.IGNORECASE),
    re.compile(r"\brisk\b", re.IGNORECASE),
    re.compile(r"\banti[\s-]?cheat\b", re.IGNORECASE),
    re.compile(r"(渗透|漏洞|攻防|红队|蓝队|隐私|风控|反作弊|反欺诈|反洗钱|黑灰产|黑产|反爬|零信任|威胁情报|恶意代码)", re.IGNORECASE),
    re.compile(r"(网络|数据|信息|应用|业务|内容|账号|账户|终端|主机|云|移动|鸿蒙|AI|大模型|模型|办公IT)安全", re.IGNORECASE),
    re.compile(r"安全[-—_]"),
    re.compile(r"安全(研发|开发|架构|渗透|攻防|响应)"),
    re.compile(r"(研发|开发|架构|渗透|攻防|响应|风控)安全"),
    re.compile(r"(安全|风控)(AI|大模型|智能体|模型)", re.IGNORECASE),
    re.compile(r"(AI|Agent|大模型|智能体)\s*(安全|红队|攻防|漏洞|威胁)", re.IGNORECASE),
]

# Weak: ambiguous — may refer to physical safety. Requires exclusion checks.
_WEAK_SECURITY_SURFACE_SIGNALS: list[re.Pattern] = [
    re.compile(r"\bsecurity\b", re.IGNORECASE),
    re.compile(r"安全方向"),
    re.compile(r"安全(运营|策略|研究|测试|产品|平台|合规|审计|专家|经理|工程师|工程|后台|运维)"),
    re.compile(r"(运营|策略|研究|测试|产品|平台|合规|审计|工程)安全"),
]

# --- Security description signals: strong vs weak ---
_STRONG_SECURITY_DESCRIPTION_SIGNALS: list[re.Pattern] = [
    re.compile(r"\bai\s*security\b", re.IGNORECASE),
    re.compile(r"\b(llm|model)\s*security\b", re.IGNORECASE),
    re.compile(r"\bcontent\s*safety\b", re.IGNORECASE),
    re.compile(r"\bsecurity\s+assessment\b", re.IGNORECASE),
    re.compile(r"\bappsec\b", re.IGNORECASE),
    re.compile(r"\bsdl\b", re.IGNORECASE),
    re.compile(r"\bdevsecops\b", re.IGNORECASE),
    re.compile(r"\bvulnerability\b", re.IGNORECASE),
    re.compile(r"\bpenetration\b", re.IGNORECASE),
    re.compile(r"\bpentest\b", re.IGNORECASE),
    re.compile(r"\bred\s*team\b", re.IGNORECASE),
    re.compile(r"\bblue\s*team\b", re.IGNORECASE),
    re.compile(r"\bsecurity\s+platform\b", re.IGNORECASE),
    re.compile(r"\bsecurity\s+engineering\b", re.IGNORECASE),
    re.compile(r"\bcloud\s*security\b", re.IGNORECASE),
    re.compile(r"\bdata\s*security\b", re.IGNORECASE),
    re.compile(r"\bwaf\b", re.IGNORECASE),
    re.compile(r"\bzero\s*trust\b", re.IGNORECASE),
    re.compile(r"\bprivacy\b", re.IGNORECASE),
    re.compile(r"\bfraud\b", re.IGNORECASE),
    re.compile(r"\brisk\b", re.IGNORECASE),
    re.compile(r"\banti[\s-]?cheat\b", re.IGNORECASE),
    re.compile(r"(渗透|漏洞|攻防|红队|蓝队|隐私|风控|反作弊|反欺诈|反洗钱|黑灰产|黑产|反爬|零信任|威胁情报|恶意代码)", re.IGNORECASE),
    re.compile(r"(网络|数据|信息|应用|业务|内容|账号|账户|终端|主机|云|移动|鸿蒙|AI|大模型|模型|办公IT)安全", re.IGNORECASE),
    re.compile(r"安全(网关|研发|开发|架构|渗透|攻防|响应)"),
]

_CYBER_SECURITY_OVERRIDE_SIGNALS: list[re.Pattern] = [
    re.compile(r"\bai\s*security\b", re.IGNORECASE),
    re.compile(r"\b(llm|model)\s*security\b", re.IGNORECASE),
    re.compile(r"\bcontent\s*safety\b", re.IGNORECASE),
    re.compile(r"\bappsec\b", re.IGNORECASE),
    re.compile(r"\bsdl\b", re.IGNORECASE),
    re.compile(r"\bdevsecops\b", re.IGNORECASE),
    re.compile(r"\bwaf\b", re.IGNORECASE),
    re.compile(r"\bzero\s*trust\b", re.IGNORECASE),
    re.compile(r"(渗透|漏洞|攻防|红队|蓝队|隐私|风控|反作弊|反欺诈|黑灰产|零信任|威胁情报|恶意代码)", re.IGNORECASE),
    re.compile(r"(网络|数据|信息|应用|内容|账号|账户|终端|主机|云|移动|鸿蒙|AI|大模型|模型|办公IT)安全", re.IGNORECASE),
]

_FUNCTIONAL_RISK_ROLE_EXCLUSIONS: list[re.Pattern] = [
    re.compile(r"(合规风控|风控合规)"),
    re.compile(r"(信贷风控岗|项目风控(?:岗|专员|经理|负责人|$))"),
    re.compile(r"(采购|供应链).*(风控|风险).*(专员|经理|负责人|专家|岗)?"),
    re.compile(
        "(\u98ce\u63a7|\u98ce\u9669|\u53cd\u6b3a\u8bc8|\u53cd\u4f5c\u5f0a).*(\u7b56\u7565|\u7ecf\u8425|\u6307\u6807|\u5546\u4e1a|\u4e1a\u52a1|\u6570\u636e|\u4ea7\u54c1|\u8fd0\u8425|\u89c4\u5219|\u7528\u6237\u5206\u5c42|\u9884\u7b97|\u589e\u957f|\u7ba1\u7406|\u5185\u63a7|\u5ba1\u8ba1|\u5408\u89c4|\u4f9b\u5e94\u94fe|\u8d44\u4ea7|EHS|\u8d22\u52a1|\u6cd5\u5f8b)"
    ),
    re.compile(
        "(\u7ecf\u8425\u5206\u6790|\u5546\u5206|\u5546\u4e1a\u5206\u6790|\u4e1a\u52a1\u5206\u6790|\u6570\u636e\u5206\u6790\u5e08|\u8d44\u4ea7\u7ba1\u7406|\u5185\u63a7\u5ba1\u8ba1|\u4f9b\u5e94\u94fe\u98ce\u63a7)"
    ),
    re.compile(
        r"(风控|风险|反欺诈|反作弊).*(策略|经营分析|商分|商业分析|业务分析|"
        r"数据分析|产品运营|运营|规则宣导|用户分层|指标分析|预算|增长)"
    ),
    re.compile(r"(经营分析|商分|商业分析|业务分析|数据分析师)"),
]

_SECURITY_ENGINEERING_OVERRIDE_SIGNALS: list[re.Pattern] = [
    re.compile(
        r"(安全|风控|反作弊|反欺诈).*(工程师|研发|开发|架构|平台|系统|后台|"
        r"运维|攻防|渗透|漏洞|应急|响应|检测|拦截)"
    ),
    re.compile(
        r"(工程师|研发|开发|架构|平台|系统|后台|运维).*(安全|风控|反作弊|反欺诈)"
    ),
    re.compile(
        r"(黑灰产|黑产|账号安全|账户安全|实时拦截|风控系统|反作弊系统|"
        r"反欺诈系统|漏洞|渗透|攻防|红队|蓝队|SDL|AppSec)",
        re.IGNORECASE,
    ),
]

_WEAK_SECURITY_DESCRIPTION_SIGNALS: list[re.Pattern] = [
    re.compile(r"安全(运营|策略|研究|测试|平台|产品|合规|审计|工程|后台)"),
]

_GENERAL_SAFETY_EXCLUSIONS: list[re.Pattern] = [
    # Physical / operational / production safety (not cybersecurity)
    re.compile(r"(交通|配送|骑手|生产|施工|消防|出行|劳动|车辆|食品|职业健康|仓储|仓库|质量|物理|作业|操作|环境|流程|运营|飞行|道路测试|功能)安全"),
    re.compile(r"(功能|自动驾驶|道路测试|无人车|无人机|飞行|车辆工程)安全"),
    # Physical security / guard / patrol roles
    re.compile(r"\bsafety\b", re.IGNORECASE),
    re.compile(r"\bphysical\s+security\b", re.IGNORECASE),
    re.compile(r"(安保|安防|安检|安全专员|安全管理员|安全保卫|安全巡视|安全巡检|安全员|安全防范|安全驾驶)"),
    re.compile(r"(门卫|警卫|保安|消防员|消防工程师)"),
]

# Surface-level clues that the role belongs to a non-cyber industry
# (logistics, retail, manufacturing, admin, autonomous driving, etc.).
# These override weak security signals when they co-occur in the title / job_type.
_NON_CYBER_ROLE_CLUES: list[re.Pattern] = [
    # Logistics, supply chain, warehouse, retail
    re.compile(r"(物流|仓储|仓库|门店|零售|供应链|前置仓|配送[中心]?|运输|货运|进出口|报关|贸易|批发|加盟|外卖|到店|到家|骑手)"),
    re.compile(r"(仓经理|仓管|订货|库存|盘点|理货|拣货|分拣|配货|打包|装卸|搬运|调度|站长)"),
    # Autonomous driving, vehicles, avionics
    re.compile(r"(无人车|自动驾驶|ADAS|底盘|轻卡|重卡|整车|车辆工程|车载|智驾|路测|路试)"),
    re.compile(r"(飞控|无人机|飞行器|航空|航天|航电|机务|地勤)"),
    # Administration, HR, finance (generic office roles)
    re.compile(r"(行政|人事|HR|财务|会计|出纳|前台|后勤|保洁|总务|秘书|助理|文员)"),
    # Manufacturing, production, processing
    re.compile(r"(生产|制造|工厂|车间|流水线|机修|电工|焊工|钳工|模具|冲压|注塑)"),
    re.compile(r"(生产设备|制造设备|设备维护|设备维修|设备管理|设备工程|"
               r"设备安装|设备调试|设备操作|设备保养|设备巡检)"),
    re.compile(r"(质检|品控|检验|检测|QA|QC|品管|品控|化验)"),
    re.compile(r"(数据中心电气|电气运维|暖通运维|机房电气)"),
    re.compile(r"(PC加工|加工[主管]?|包装|灌装|组装|车工|铣工)"),
    # Construction / civil engineering (physical, not cyber)
    re.compile(r"(工程项目|土木|建筑|施工|装修|监理|造价|预算|给排水|暖通|结构|施工员)"),
    # Operations / business roles
    re.compile(r"(门店运营|城市运营|区域运营|业务运营|运营主管|运营经理|城市经理|区域经理|开拓经理|拓展经理)"),
    re.compile(r"(订单|客服|售后|门店经理|导购|收银|店长)"),
]

_WEAK_AI_USAGE: list[re.Pattern] = [
    re.compile(r"(AI工具|AI\s*工具|AI辅助|借助AI|运用AI工具|用AI|AI技术.*关注|AI商业应用)", re.IGNORECASE),
    re.compile(r"(AI\s*Coding|AI辅助编码|AI编程工具|AI编程辅助|Copilot|Cursor|Claude Code)", re.IGNORECASE),
]

_NON_TARGET_SURFACE_ROLES: list[re.Pattern] = [
    # Commercial, sales, business, admin
    re.compile(r"(销售|商务|渠道|客户经理|采购|公共事务|政府关系|市场|行政|财务|职能|综合|内控|HRBP|人力资源)"),
    re.compile(r"(业务拓展|商业拓展|\bBD\b|生态|客户成功|大客户)", re.IGNORECASE),
    # Non-engineering operational roles
    re.compile(r"(运营|测试|项目经理|产品经理|产品运营|审核|数据分析|数据运营|补贴|供给)"),
    # Content, editorial, technical writing
    re.compile(r"(内容|内容运营|内容开发|技术写作|技术内容|技术编辑)"),
    # Design / UI / UX
    re.compile(r"(设计|设计师|\bUI\b|\bUX\b|视觉|交互设计)"),
    # Developer community / ecosystem operations
    re.compile(r"(社区运营|开发者社区|开发者运营|社区经理)"),
    # Generic intern
    re.compile(r"实习"),
    # Logistics, retail
    re.compile(r"(物流|零售|门店|仓|供应链/零售|门店/零售|物流/零售)"),
]

_HARD_NON_TARGET_SURFACE_ROLES: list[re.Pattern] = [
    re.compile(
        r"(销售|Sale|Sales|商务|渠道|客户经理|客户成功|大客户|业务拓展|商业拓展|\bBD\b|"
        r"Business\s*Development|Ecosystem|生态|公共事务|政府关系|市场|Marketing|"
        r"Commercialization|Partner\s*Operations|Partnerships?|Growth\s*Manager)",
        re.IGNORECASE,
    ),
    re.compile(r"(供应商管理|供应商|资源管理|资源采购|采购)", re.IGNORECASE),
    re.compile(r"(内容|内容运营|内容开发|技术写作|技术内容|技术编辑|社媒|YouTube)", re.IGNORECASE),
    re.compile(r"(设计|设计师|\bUI\b|\bUX\b|视觉|交互设计)", re.IGNORECASE),
    re.compile(r"(社区运营|开发者社区|开发者运营|社区经理)", re.IGNORECASE),
    re.compile(r"(运营(?!商)|社媒|营销|增长|发行)", re.IGNORECASE),
    re.compile(r"(项目经理|交付项目|项目/产品经理|项目管理)", re.IGNORECASE),
    re.compile(r"(数据标注|数据分析师|数据分析|数据挖掘|QA|测试|审核)", re.IGNORECASE),
    re.compile(r"(专利|知识产权|律师|法务)", re.IGNORECASE),
    re.compile(r"实习"),
]

_ALWAYS_EXCLUDED_SURFACE_ROLES: list[re.Pattern] = [
    re.compile(
        r"(销售|Sale|Sales|商务|渠道|客户经理|客户成功|大客户|业务拓展|商业拓展|\bBD\b|"
        r"Business\s*Development|Ecosystem|生态|公共事务|政府关系|市场|Marketing)",
        re.IGNORECASE,
    ),
    re.compile(r"(内容|内容运营|内容开发|技术写作|技术内容|技术编辑|社媒|YouTube)", re.IGNORECASE),
    re.compile(r"(设计|设计师|\bUI\b|\bUX\b|视觉|交互设计)", re.IGNORECASE),
    re.compile(r"(社区运营|开发者社区|开发者运营|社区经理)", re.IGNORECASE),
    re.compile(r"(项目经理|交付项目|项目/产品经理|项目管理|PMO)", re.IGNORECASE),
    re.compile(r"(招聘|猎头|人才发展|客服|客户服务|技术支持|售前|HRG|Finance|Procurement)", re.IGNORECASE),
    re.compile(r"Customer\s+Experience\s+Operations?", re.IGNORECASE),
    re.compile(r"(教研员|教研|教师|讲师|医学研究员)", re.IGNORECASE),
    re.compile(r"(专利|知识产权|律师|法务)", re.IGNORECASE),
    re.compile(r"(实习|\bIntern(?:ship)?\b)", re.IGNORECASE),
    # Phase 2A strict surface exclusions (requirement 5)
    re.compile(r"(产品经理|产品.*运营|产品负责人|产品专家|产品.*规划|产品.*战略|策略产品|产品架构方向)"),
    re.compile(r"(策略运营|风控运营|业务运营|经营分析|经济安全.*运营|系统策划.*安全)"),
    re.compile(r"(数据分析师|数据分析|数据运营|经营分析师|业务分析师|商业分析师)"),
    re.compile(r"(产品策划|系统策划|技术策划|玩法策划|载具策划|创意策划|内容策划|活动策划|品牌策划|AI策划|主策|策划师|策划类|策划$)"),
    re.compile(r"(战略合作|组织治理|法律方向)"),
    re.compile(r"(\bDBA\b|数据库工程师|车机技术架构师)", re.IGNORECASE),
    re.compile(r"AI\s*Builder.*策略方向", re.IGNORECASE),
]

_COMMERCIAL_AI_EXCLUSIONS: list[re.Pattern] = [
    re.compile(r"(销售|商务|渠道|客户经理|大客户|客户开拓|回款|市场与销售|售前)"),
    re.compile(r"(公共事务|政府关系|资本市场|融资|投资者关系|市场经理|市场专员)"),
]

_NON_ENGINEERING_AI_EXCLUSIONS: list[re.Pattern] = [
    re.compile(r"(产品经理|产品负责人|产品专家|产品规划|产品运营|产品实习|运营实习|业务助理|内容开发|内容运营|课程|讲师|教师)"),
    re.compile(
        r"\b(Product\s+(?:Manager|Management|Owner)|Talent\s+Acquisition|"
        r"Recruiter|Recruiting|Operations\s+Lead)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(数据分析师|QA|测试|质量管理|审核)"),
    re.compile(r"(consultant|consulting|咨询|调研|顾问)", re.IGNORECASE),
]

_ALGORITHM_KEYWORD = re.compile(r"算法")
_REQUIREMENT_MARKERS = (
    "岗位要求:",
    "岗位要求：",
    "任职要求:",
    "任职要求：",
    "要求:",
    "要求：",
    "资格要求:",
    "资格要求：",
)
_RESPONSIBILITY_MARKERS = (
    "职责:",
    "职责：",
    "岗位职责:",
    "岗位职责：",
    "工作职责:",
    "工作职责：",
    "岗位描述:",
    "岗位描述：",
)


def _matches_any(text: str, patterns: list[re.Pattern]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _before_first_marker(text: str, markers: tuple[str, ...]) -> str:
    indexes = [text.find(marker) for marker in markers if marker in text]
    if not indexes:
        return text
    return text[: min(indexes)]


def _after_marker(text: str, marker: str) -> str:
    return text[text.find(marker) + len(marker) :]


def _primary_description(description: str) -> str:
    """Return the responsibility-like part of a combined job description."""
    sections: list[str] = []
    for marker in _RESPONSIBILITY_MARKERS:
        if marker in description:
            section = _after_marker(description, marker)
            sections.append(_before_first_marker(section, _REQUIREMENT_MARKERS))

    if sections:
        return " ".join(section.strip() for section in sections if section.strip())

    return _before_first_marker(description, _REQUIREMENT_MARKERS).strip()


def _has_non_cyber_safety(surface: str) -> bool:
    return _matches_any(surface, _GENERAL_SAFETY_EXCLUSIONS)


def _is_non_target_surface_role(surface: str) -> bool:
    return _matches_any(surface, _NON_TARGET_SURFACE_ROLES)


def _is_hard_non_target_surface_role(surface: str) -> bool:
    return _matches_any(surface, _HARD_NON_TARGET_SURFACE_ROLES)


def _is_always_excluded_surface_role(surface: str) -> bool:
    return _matches_any(surface, _ALWAYS_EXCLUDED_SURFACE_ROLES)


def _is_commercial_ai_exclusion(surface: str) -> bool:
    return _matches_any(surface, _COMMERCIAL_AI_EXCLUSIONS)


def _is_non_engineering_ai_exclusion(surface: str) -> bool:
    return _matches_any(surface, _NON_ENGINEERING_AI_EXCLUSIONS)


def _is_likely_non_cyber_role(surface: str) -> bool:
    """Check if the surface (title + job_type) suggests a non-cybersecurity industry."""
    return _matches_any(surface, _NON_CYBER_ROLE_CLUES)


def _is_functional_risk_role(text: str) -> bool:
    """Return True for business risk/control strategy roles, not security work."""
    return _matches_any(text, _FUNCTIONAL_RISK_ROLE_EXCLUSIONS)


def _has_security_engineering_override(text: str) -> bool:
    """Return True when functional risk terms are tied to concrete security work."""
    return _matches_any(text, _SECURITY_ENGINEERING_OVERRIDE_SIGNALS)


def _has_description_ai(description: str) -> bool:
    if _matches_any(description, _WEAK_AI_USAGE) and not _matches_any(
        description, _AI_DESCRIPTION_SIGNALS
    ):
        return False
    return _matches_any(description, _AI_DESCRIPTION_SIGNALS)


# ---------------------------------------------------------------------------
# Hard-exclusion surface patterns  (Phase 2A — requirement 5)
# ---------------------------------------------------------------------------
# These patterns are checked against the SURFACE (title + job_type) only, in
# the order listed.  The first match wins.  All categories listed in
# requirement 5 are represented: algorithm, product, business/risk ops,
# analysis, planning, sales, project, intern, QA/design/legal/admin.
# Category "qa_design_legal_admin" may be overridden by explicit security
# engineering surface signals (requirement 8 target scope).

_HARD_EXCLUSION_REASONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"算法"), REASON_ALGORITHM),
    (re.compile(r"(\bDBA\b|数据库工程师|车机技术架构师)", re.IGNORECASE), REASON_NON_TARGET_INFRASTRUCTURE),
    (re.compile(
        r"(产品经理|产品负责人|产品专家|产品.*规划|产品.*运营|产品.*战略|"
        r"策略产品|产品架构方向|\bProduct\s+(?:Manager|Management|Owner)\b|"
        r"\bOperations\s+Lead\b)",
        re.IGNORECASE,
    ), REASON_PRODUCT),
    (re.compile(r"(策略运营|风控运营|业务运营|经营分析|经济风控|"
                r"经济安全.*运营|系统策划.*安全|风险策略运营|业务风控|"
                r"战略合作|组织治理|合规风控|风控合规)"), REASON_BUSINESS_OPS),
    (re.compile(r"(数据分析师|数据分析|数据运营|数据标注|"
                r"经营分析师|业务分析师|商业分析师|BI分析师)"), REASON_ANALYSIS),
    (re.compile(r"(产品策划|系统策划|创意策划|内容策划|活动策划|品牌策划|AI策划|主策|策划师|策划类|策划$)"), REASON_PLANNING),
    (re.compile(r"(销售|Sale|Sales|商务|商务拓展|渠道|客户经理|客户成功|"
                r"大客户|业务拓展|商业拓展|\bBD\b|市场|Marketing|市场经理|"
                r"营销|售前)", re.IGNORECASE), REASON_SALES),
    (re.compile(r"(项目经理|项目管理|PMO|交付经理|交付项目)"), REASON_PROJECT),
    (re.compile(r"实习"), REASON_INTERN),
    # QA / audit / content / design / legal / admin  (overrideable)
    (re.compile(r"(QA|测试|审核|审计|内容运营|内容编辑|编辑|文案|"
                r"设计|设计师|\bUI\b|\bUX\b|视觉|交互设计|法务|律师|"
                r"法律方向|行政|职能|HR|招聘|Talent\s+Acquisition|Recruiter|Recruiting|"
                r"客服|技术支持)", re.IGNORECASE), REASON_QA_DESIGN_LEGAL),
]

# Security-engineering override patterns  (allow penetration testing & similar
# security roles that happen to contain a QA-like keyword to remain target).
_SECURITY_ENGINEERING_OVERRIDE: list[re.Pattern] = [
    re.compile(r"(渗透|漏洞(?!\s*[a-z])|攻防|红队|蓝队|WAF|零信任|"
               r"AppSec|SDL|DevSecOps|安全渗透|安全测试|安全研发|"
               r"安全开发|安全架构|安全响应)"),
]

# ---------------------------------------------------------------------------
# Technical surface patterns  (requirement 6 — "engineering-looking" titles)
# ---------------------------------------------------------------------------
# These match general engineering / development / architecture / system
# wording in the title or job_type.  Used in Step 3 of
# :func:`classify_job_detailed` to decide whether a job whose tags come
# solely from *description* signals should be promoted to ``review`` status.
# A plain product/operations/strategy title does NOT match here, so it
# cannot be upgraded by description evidence alone.

_TECHNICAL_SURFACE_PATTERNS: list[re.Pattern] = [
    re.compile(r"(工程师|工程(?!.*设计)|研发|开发|架构师|架构|"
               r"平台|后端|系统|内核|中间件|基础设施|infra|"
               r"后台|全栈|算法(?!.*产品|.*运营|.*分析))"),
    re.compile(r"\b(engineer|developer|architect|platform|backend|system|"
               r"infra|infrastructure|full.stack|backend|detection|response|"
               r"vulnerability|penetration|offensive|defensive|appsec|sdl|"
               r"devsecops)\b", re.IGNORECASE),
]

_EXPLICIT_ENGINEERING_SURFACE_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(工程师|研发|开发|架构师|架构|后端|前端|客户端|服务端|全栈|"
        r"技术负责人|技术专家|测试开发|检测|响应|渗透|漏洞|攻防)"
    ),
    re.compile(
        r"\b(engineer|developer|architect|backend|frontend|full.stack|FDE|"
        r"detection|response|vulnerability|penetration|offensive|defensive)\b",
        re.IGNORECASE,
    ),
]

_NON_ENGINEERING_PRODUCT_SURFACE = re.compile(
    r"(产品(?:经理|负责人|专家|运营|策划|战略|规划|架构|方案|解决方案|美学|交付)|"
    r"(?:体验|搜索|平台|应用|风控|CRM)产品(?:\b|[-（(])|"
    r"AI产品(?:\b|[-（(])|产品(?:\s*$|[-（(])|"
    r"\bproduct\s+(?:manager|management|owner|lead|strategy|operations?|solutions?)\b)",
    re.IGNORECASE,
)

_HIGH_CONFIDENCE_AI_SURFACE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(MLOps|LLMOps|Agentic\s+Infra)\b", re.IGNORECASE),
    re.compile(r"\b(?:AI|Agent)\s+Builder\b", re.IGNORECASE),
    re.compile(r"(模型推理|推理部署|推理优化|推理系统|推理框架|模型服务)"),
    re.compile(r"(训练.{0,6}(?:框架|平台|系统)|框架训练|平台训练|高性能计算|异构计算)"),
    re.compile(r"(AI|大模型|智能体)(平台|基础设施|Infra|算力|安全)", re.IGNORECASE),
    re.compile(r"(AI安全|大模型安全|模型安全|智能体安全).*(研究|Research)", re.IGNORECASE),
    re.compile(r"\bAI\s+Security\s+Research", re.IGNORECASE),
    re.compile(r"(AI|Agent|大模型|智能体)\s*(安全|红队|攻防|漏洞|威胁)", re.IGNORECASE),
]

_ALGORITHM_ADJACENT_AI_SURFACE = re.compile(
    r"(建模|模型架构|模型研发|模型训练|大模型训练|预训练|后训练|微调|对齐|强化学习|机器学习|深度学习|量化投研|投研|多模态.*(?:研究员|架构))"
)


def _has_technical_surface(surface: str) -> bool:
    """Return True when the surface (title + job_type) reads as engineering."""
    return _matches_any(surface, _TECHNICAL_SURFACE_PATTERNS)


def _has_explicit_engineering_surface(surface: str) -> bool:
    return _matches_any(surface, _EXPLICIT_ENGINEERING_SURFACE_PATTERNS)


def _is_non_engineering_product_surface(surface: str) -> bool:
    return bool(_NON_ENGINEERING_PRODUCT_SURFACE.search(surface)) and not (
        _has_explicit_engineering_surface(surface)
    )


def _has_high_confidence_ai_surface(surface: str) -> bool:
    return _matches_any(surface, _HIGH_CONFIDENCE_AI_SURFACE_PATTERNS)


_FUNCTIONAL_SECURITY_REVIEW_SURFACES: list[re.Pattern] = [
    re.compile(r"(运营|治理|合规|审计|策略)"),
    re.compile(r"(?:^|[^A-Za-z])BP(?:$|[^A-Za-z])", re.IGNORECASE),
    re.compile(r"\b(operations?|governance|compliance|audit|strategy|GRC)\b", re.IGNORECASE),
]


def _is_functional_security_review_surface(surface: str) -> bool:
    """Return whether a security title is functional rather than engineering."""
    return _matches_any(surface, _FUNCTIONAL_SECURITY_REVIEW_SURFACES)


_FUNCTIONAL_ROLE_ENGINEERING_SURFACES: list[re.Pattern] = [
    re.compile(r"(工程师|研发|开发|架构师|架构|后端|前端|客户端|服务端|全栈|测试开发)"),
    re.compile(r"\b(engineer|developer|architect|backend|frontend|full.stack|FDE)\b", re.IGNORECASE),
]


def _has_functional_role_engineering_surface(surface: str) -> bool:
    return _matches_any(surface, _FUNCTIONAL_ROLE_ENGINEERING_SURFACES)


def _get_exclusion_reason(
    surface: str, description: str = "", title: str = ""
) -> str:
    """Return a reason code explaining why *classify_job* returned empty tags.

    This is purely diagnostic — the exclusion decision itself is made by
    :func:`classify_job`.  The first pattern match wins (priority order).
    """
    if _is_non_engineering_product_surface(title or surface):
        return REASON_PRODUCT
    for pattern, reason in _HARD_EXCLUSION_REASONS:
        if pattern.search(surface):
            if reason == REASON_QA_DESIGN_LEGAL:
                # Check for security engineering override
                if _matches_any(surface, _SECURITY_ENGINEERING_OVERRIDE):
                    continue  # override — skip to next pattern
            return reason
    return REASON_NO_SIGNALS


def _determine_target_reasons(surface: str) -> tuple[str, ...]:
    """Build reason codes for a ``target`` job based on surface signals."""
    reasons: list[str] = []
    has_ai = _matches_any(surface, _AI_SURFACE_SIGNALS)
    has_sec = _matches_any(
        surface, [*_STRONG_SECURITY_SURFACE_SIGNALS, *_WEAK_SECURITY_SURFACE_SIGNALS]
    )
    if has_ai:
        reasons.append(REASON_AI_SURFACE)
    if has_sec:
        reasons.append(REASON_SECURITY_SURFACE)
    if has_ai and has_sec:
        reasons.append(REASON_AI_SECURITY_SURFACE)
    return tuple(reasons)


def _determine_review_reasons(tags: tuple[str, ...]) -> tuple[str, ...]:
    """Build reason codes for a ``review`` job based on its tags."""
    reasons: list[str] = []
    if "AI" in tags:
        reasons.append(REASON_REVIEW_AI)
    if "Security" in tags:
        reasons.append(REASON_REVIEW_SECURITY)
    if "AI" in tags and "Security" in tags:
        reasons.append(REASON_REVIEW_AI_SECURITY)
    return tuple(reasons)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_job_detailed(
    title: str, description: str = "", job_type: str = ""
) -> DetailedClassification:
    """Classify a job and return a frozen :class:`DetailedClassification`.

    The logic always defers to :func:`classify_job` for the tag decision so
    the two APIs remain consistent.  This function then layers on relevance
    status and machine-readable reason codes:

    * **tags non-empty + surface has AI/security signals** → ``target``
    * **tags non-empty + no surface signals + engineering title +
      description evidence** → ``review``
    * **tags empty** → ``excluded`` (with a reason code)
    """
    title = title or ""
    description = description or ""
    job_type = job_type or ""
    surface = f"{title} {job_type}".strip() if (title or job_type) else ""

    # Always defer classify_job for tag correctness.
    tags = classify_job(title, description, job_type)

    if not tags:
        return DetailedClassification(
            tags=(),
            relevance_status="excluded",
            reasons=(_get_exclusion_reason(surface, description, title),),
        )

    if (
        "Security" in tags
        and _is_functional_security_review_surface(surface)
        and not _has_functional_role_engineering_surface(surface)
    ):
        return DetailedClassification(
            tags=tuple(tags),
            relevance_status="review",
            reasons=(REASON_FUNCTIONAL_SECURITY_REVIEW,),
        )

    if (
        "AI" in tags
        and _matches_any(surface, _AI_SURFACE_SIGNALS)
        and not _has_explicit_engineering_surface(surface)
        and not _has_high_confidence_ai_surface(surface)
    ):
        return DetailedClassification(
            tags=tuple(tags),
            relevance_status="review",
            reasons=(REASON_AMBIGUOUS_AI_REVIEW,),
        )

    if (
        "AI" in tags
        and _matches_any(surface, _AI_SURFACE_SIGNALS)
        and _ALGORITHM_ADJACENT_AI_SURFACE.search(surface)
        and not _has_high_confidence_ai_surface(surface)
    ):
        return DetailedClassification(
            tags=tuple(tags),
            relevance_status="review",
            reasons=(REASON_ALGORITHM_ADJACENT_AI_REVIEW,),
        )

    # Tags exist — distinguish target (surface signals) from review
    # (engineering title + description evidence).
    surface_has_signals = _matches_any(
        surface,
        [*_AI_SURFACE_SIGNALS, *_STRONG_SECURITY_SURFACE_SIGNALS, *_WEAK_SECURITY_SURFACE_SIGNALS],
    )
    if surface_has_signals:
        return DetailedClassification(
            tags=tuple(tags),
            relevance_status="target",
            reasons=_determine_target_reasons(surface),
        )

    # Engineering-looking title with description evidence → review.
    primary_desc = _primary_description(description)
    has_desc_ai = _has_description_ai(primary_desc)
    has_desc_sec = _matches_any(
        primary_desc, [*_STRONG_SECURITY_DESCRIPTION_SIGNALS, *_WEAK_SECURITY_DESCRIPTION_SIGNALS]
    )
    if _has_technical_surface(surface) and (has_desc_ai or has_desc_sec):
        return DetailedClassification(
            tags=tuple(tags),
            relevance_status="review",
            reasons=_determine_review_reasons(tuple(tags)),
        )

    # Fallback (should not normally be reached): treat as review.
    return DetailedClassification(
        tags=tuple(tags),
        relevance_status="review",
        reasons=_determine_review_reasons(tuple(tags)),
    )


def classify_job(title: str, description: str, job_type: str = "") -> list[str]:
    """Classify a job into ``AI``, ``Security``, and ``AI Security`` tags.

    This is the legacy API — it returns tags only, not the full
    :class:`DetailedClassification`.  For target and review jobs the returned
    tags are identical; for excluded jobs the list is empty.

    .. seealso::
        :func:`classify_job_detailed` for the full result with relevance
        status, machine-readable reasons, and classification version.
    """
    title = title or ""
    description = description or ""
    job_type = job_type or ""
    surface = f"{title} {job_type}"
    primary_description = _primary_description(description)

    ai_role_exclusion = _is_commercial_ai_exclusion(
        surface
    ) or _is_non_engineering_ai_exclusion(surface)
    non_target_surface_role = _is_non_target_surface_role(surface)
    hard_non_target_surface_role = _is_hard_non_target_surface_role(surface)
    always_excluded_surface_role = _is_always_excluded_surface_role(surface)
    non_cyber_surface = _has_non_cyber_safety(surface) or _is_likely_non_cyber_role(
        surface
    )
    strong_target_ai_surface = _matches_any(surface, _TARGET_AI_SURFACE_SIGNALS)
    surface_ai = _matches_any(surface, _AI_SURFACE_SIGNALS) and (
        not non_target_surface_role or strong_target_ai_surface
    )
    description_ai = (
        not non_target_surface_role
        and not non_cyber_surface
        and _has_description_ai(primary_description)
    )
    has_ai = surface_ai or description_ai

    algorithm_role = bool(_ALGORITHM_KEYWORD.search(surface))
    if algorithm_role:
        return []
    if always_excluded_surface_role:
        return []
    if "职能" in job_type and not _has_explicit_engineering_surface(title):
        return []
    if _is_non_engineering_product_surface(title):
        return []
    surface_functional_risk_role = _is_functional_risk_role(
        surface
    ) and not _has_functional_role_engineering_surface(surface)
    if surface_functional_risk_role:
        return []
    functional_risk_role = _is_functional_risk_role(
        f"{surface} {primary_description}"
    ) and not _has_security_engineering_override(f"{surface} {primary_description}")
    if functional_risk_role:
        return []

    security_surface = _matches_any(
        surface,
        _STRONG_SECURITY_SURFACE_SIGNALS + _WEAK_SECURITY_SURFACE_SIGNALS,
    )
    if (ai_role_exclusion or hard_non_target_surface_role) and not security_surface:
        return []

    non_cyber_description = _has_non_cyber_safety(
        primary_description
    ) or _is_likely_non_cyber_role(primary_description)

    strong_surface_security = _matches_any(
        surface, _STRONG_SECURITY_SURFACE_SIGNALS
    ) and (
        not non_cyber_surface
        or _matches_any(surface, _CYBER_SECURITY_OVERRIDE_SIGNALS)
    )
    weak_surface_security = (
        not non_cyber_surface
        and (
            not non_cyber_description
            or _has_security_engineering_override(surface)
        )
        and _matches_any(surface, _WEAK_SECURITY_SURFACE_SIGNALS)
    )
    strong_description_security = (
        not non_target_surface_role
        and not non_cyber_surface
        and not non_cyber_description
        and _matches_any(primary_description, _STRONG_SECURITY_DESCRIPTION_SIGNALS)
    )
    weak_description_security = (
        not non_target_surface_role
        and not non_cyber_surface
        and not non_cyber_description
        and _matches_any(primary_description, _WEAK_SECURITY_DESCRIPTION_SIGNALS)
    )
    has_security = (
        strong_surface_security
        or weak_surface_security
        or strong_description_security
        or weak_description_security
    )

    if surface_ai and not security_surface:
        has_security = False
    if security_surface and not surface_ai:
        has_ai = False

    tags: list[str] = []
    if has_ai:
        tags.append("AI")
    if has_security:
        tags.append("Security")
    if has_ai and has_security:
        tags.append("AI Security")
    return tags
