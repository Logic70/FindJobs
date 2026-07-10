"""Real-data classification regression tests."""

import pytest

from findjobs.classify import (
    CLASSIFICATION_VERSION,
    REASON_ALGORITHM_ADJACENT_AI_REVIEW,
    REASON_AMBIGUOUS_AI_REVIEW,
    REASON_FUNCTIONAL_SECURITY_REVIEW,
    classify_job,
    classify_job_detailed,
)


@pytest.mark.parametrize(
    "title",
    [
        "AI Talent Acquisition Lead / Partner",
        "AI Product Manager Intern 107654",
        "Sr. Product Manager, AI Infrastructure",
        "Product Manager - AI Creator Platform (WorkSolo)",
        "Game AI Product Management Intern 107504",
        "Operations Lead - AI Creator Platform (WorkSolo)",
        "互联网信贷风控策略-风控",
        "搜索策略产品（AI Builder）",
        "小团Agent策略产品 AI Builder",
        "运营商AI产品战略合作-火山引擎",
        "CPO线-组织治理专家-AI Builder",
        "阿里控股-大模型评测专家-法律方向",
        "阿里控股-AI产品战略规划专家-杭州",
        "合规风控专家（游戏营销）",
    ],
)
def test_real_false_positive_titles_are_excluded(title):
    description = "负责 AI 平台、风控系统和大模型安全相关工作。"

    result = classify_job_detailed(title, description)

    assert result.relevance_status == "excluded"
    assert result.tags == ()
    assert result.reasons
    assert result.version == CLASSIFICATION_VERSION
    assert classify_job(title, description) == []


@pytest.mark.parametrize(
    "title",
    [
        "安全运营工程师",
        "数据安全运营工程师-权限运营方向",
        "渗透测试工程师",
        "资深安全测试工程师-豆包手机助手",
        "业务风控测试开发专家-TikTok",
        "Agent开发架构师（AI Testing）-开发者服务",
        "集团安全部-AI测试工程师-风控",
        "到餐-全栈 Agent Builder",
    ],
)
def test_real_engineering_titles_remain_targets(title):
    result = classify_job_detailed(title, "负责系统研发、检测响应和平台建设。")

    assert result.relevance_status == "target"
    assert result.tags
    assert result.reasons
    assert result.version == CLASSIFICATION_VERSION


@pytest.mark.parametrize(
    "title",
    [
        "安全运营专家",
        "隐私安全治理运营专家",
        "Agent安全运营",
        "蓝军攻防运营专家",
        "数据安全技术运营",
    ],
)
def test_non_engineering_security_operations_require_review(title):
    result = classify_job_detailed(title, "负责安全运营、风险治理和应急协同。")

    assert result.relevance_status == "review"
    assert "Security" in result.tags
    assert result.reasons == (REASON_FUNCTIONAL_SECURITY_REVIEW,)
    assert result.version == CLASSIFICATION_VERSION


def test_description_engineering_words_do_not_rescue_functional_risk_title():
    result = classify_job_detailed(
        "互联网信贷风控策略-风控",
        "负责后台平台、系统架构和风控策略运营。",
    )

    assert result.relevance_status == "excluded"
    assert result.tags == ()


@pytest.mark.parametrize("title", ["DBA数据库工程师", "车机技术架构师"])
def test_known_generic_infrastructure_false_positives_are_excluded(title):
    result = classify_job_detailed(
        title,
        "负责大模型平台、推理部署、Agent 应用和系统架构建设。",
    )

    assert result.relevance_status == "excluded"
    assert result.tags == ()


@pytest.mark.parametrize(
    "title",
    [
        "AI智能服务体验产品（商家与创作者方向）",
        "AI搜索高阶产品",
        "智能风控平台产品",
        "大模型安全产品-豆包对话",
    ],
)
def test_non_engineering_product_titles_are_excluded(title):
    result = classify_job_detailed(title, "负责 AI 产品规划和平台协作。")

    assert result.relevance_status == "excluded"
    assert result.tags == ()


def test_product_department_name_does_not_exclude_ai_evaluation_role():
    result = classify_job_detailed(
        "数据技术及产品部-语音大模型评测专家-杭州/北京",
        "负责大模型评测体系和数据质量建设。",
    )

    assert result.relevance_status == "review"
    assert "AI" in result.tags


@pytest.mark.parametrize(
    "title",
    [
        "大模型产品解决方案架构师-火山方舟MaaS",
        "AI产品解决方案架构师-杭州",
        "服务端开发工程师（AI应用开发方向）-产品研发和工程架构",
        "风控合规研发工程师（方向）-国际支付",
        "AI 应用工程师（游戏策划客户端Agent方向）",
        "数据技术及产品部-AI 视频领域数据架构师",
    ],
)
def test_engineering_role_context_is_not_mistaken_for_functional_product(title):
    result = classify_job_detailed(title, "负责大模型应用、平台架构和工程交付。")

    assert result.relevance_status == "target"
    assert result.tags


@pytest.mark.parametrize(
    "title",
    [
        "AI解决方案专家",
        "大模型数据策略专家",
        "AI应用方向负责人",
        "AI客户体验专家",
    ],
)
def test_ambiguous_non_engineering_ai_titles_require_review(title):
    result = classify_job_detailed(title, "负责大模型应用落地和客户场景分析。")

    assert result.relevance_status == "review"
    assert "AI" in result.tags
    assert result.reasons == (REASON_AMBIGUOUS_AI_REVIEW,)


@pytest.mark.parametrize(
    "title",
    [
        "MLOps",
        "Agentic Infra",
        "AI Builder",
        "大模型推理优化",
        "AI平台负责人",
        "大模型算力负责人",
    ],
)
def test_high_confidence_ai_surfaces_remain_targets_without_engineer_suffix(title):
    result = classify_job_detailed(title, "负责平台建设、推理部署和服务稳定性。")

    assert result.relevance_status == "target"
    assert "AI" in result.tags


@pytest.mark.parametrize(
    "title",
    ["AI建模开发工程师", "大模型训练工程师", "AI量化投研工程师-Agent方向"],
)
def test_algorithm_adjacent_ai_engineering_requires_review(title):
    result = classify_job_detailed(title, "负责模型训练、对齐和效果优化。")

    assert result.relevance_status == "review"
    assert result.reasons == (REASON_ALGORITHM_ADJACENT_AI_REVIEW,)


def test_training_framework_engineering_remains_target():
    result = classify_job_detailed(
        "大模型训练框架开发工程师",
        "负责训练平台和分布式框架研发。",
    )

    assert result.relevance_status == "target"
    assert result.tags == ("AI",)


def test_training_inference_platform_engineering_remains_target():
    result = classify_job_detailed(
        "大模型训练推理平台开发工程师",
        "负责训练推理平台和分布式系统研发。",
    )

    assert result.relevance_status == "target"
    assert result.tags == ("AI",)


def test_ai_role_does_not_gain_security_tag_from_description_only():
    result = classify_job_detailed(
        "AI应用工程师-跨境支付",
        "负责反欺诈系统、风控拦截和大模型应用开发。",
    )

    assert result.relevance_status == "target"
    assert result.tags == ("AI",)


def test_security_role_does_not_gain_ai_tag_from_description_only():
    result = classify_job_detailed(
        "数据安全研发工程师",
        "负责大模型平台的数据安全研发和访问控制。",
    )

    assert result.relevance_status == "target"
    assert result.tags == ("Security",)


def test_ai_security_surface_keeps_both_tags():
    result = classify_job_detailed(
        "AI安全工程师",
        "负责大模型安全攻防和平台研发。",
    )

    assert result.relevance_status == "target"
    assert result.tags == ("AI", "Security", "AI Security")


def test_security_large_model_surface_keeps_both_tags():
    result = classify_job_detailed(
        "安全大模型技术专家",
        "负责安全大模型应用、推理部署和风险检测。",
    )

    assert result.relevance_status == "target"
    assert result.tags == ("AI", "Security", "AI Security")


@pytest.mark.parametrize(
    "title",
    ["Ant International-Security GRC Specialist", "隐私技术检测BP"],
)
def test_non_engineering_security_governance_titles_require_review(title):
    result = classify_job_detailed(title, "负责安全治理、隐私合规和风险协同。")

    assert result.relevance_status == "review"
    assert "Security" in result.tags


def test_procurement_risk_specialist_is_excluded():
    result = classify_job_detailed(
        "资深采购风控专员",
        "负责供应商风险管理、采购内控和指标分析。",
    )

    assert result.relevance_status == "excluded"
    assert result.tags == ()


@pytest.mark.parametrize(
    "title",
    [
        "AGI研发工程师",
        "电商场景LLM/VLM/AIGC推理工程师",
        "大模型训练系统工程师",
        "深度学习高性能计算研发工程师",
    ],
)
def test_ai_acronyms_and_systems_next_to_cjk_are_target(title):
    result = classify_job_detailed(title, "负责 AI 基础设施和推理系统研发。")

    assert result.relevance_status == "target"
    assert "AI" in result.tags


@pytest.mark.parametrize(
    "title",
    ["AI 安全研究员", "AI红队安全专家", "Agent安全-北京"],
)
def test_ai_security_high_confidence_surfaces_are_target(title):
    result = classify_job_detailed(title, "负责智能体安全研究、红队攻防和漏洞治理。")

    assert result.relevance_status == "target"
    assert result.tags == ("AI", "Security", "AI Security")


@pytest.mark.parametrize(
    "title",
    [
        "AI Agent Research & Application Intern",
        "人才发展专家-安全合规线",
        "Partnerships Growth Manager",
        "产品交付专家（云和安全）",
    ],
)
def test_additional_functional_titles_are_excluded(title):
    result = classify_job_detailed(title, "负责 AI、安全、云平台和业务协同。")

    assert result.relevance_status == "excluded"
    assert result.tags == ()


@pytest.mark.parametrize(
    ("title", "job_type"),
    [
        ("阿里云智能-云安全HRG专家", ""),
        ("安全解决方案售前-安全与风控", "安全"),
        ("易盾-政企安全专家", "职能"),
    ],
)
def test_security_named_functional_roles_are_excluded(title, job_type):
    result = classify_job_detailed(
        title,
        "负责安全业务合作、客户拓展、人才组织和政府关系。",
        job_type,
    )

    assert result.relevance_status == "excluded"
    assert result.tags == ()


@pytest.mark.parametrize("title", ["大模型架构工程师-抖音电商", "大模型预训练架构工程师"])
def test_model_architecture_and_pretraining_roles_require_review(title):
    result = classify_job_detailed(title, "负责模型设计、训练和效果优化。")

    assert result.relevance_status == "review"
    assert result.reasons == (REASON_ALGORITHM_ADJACENT_AI_REVIEW,)


def test_data_center_electrical_operations_is_not_security():
    result = classify_job_detailed(
        "数据中心电气运维技术专家",
        "负责机房电气、供配电和基础设施安全运行。",
    )

    assert result.relevance_status == "excluded"
    assert result.tags == ()


def test_unreliable_product_job_type_does_not_hide_ai_security_title():
    result = classify_job_detailed(
        "集团安全部-智能体安全专家-杭州",
        "负责智能体安全风险识别、攻防评估和安全治理。",
        "产品",
    )

    assert result.relevance_status == "target"
    assert result.tags == ("AI", "Security", "AI Security")


@pytest.mark.parametrize(
    "title",
    [
        "《王者荣耀世界》技术策划(AI方向)",
        "《王者荣耀》-AI玩法策划",
        "资深载具策划（明日之后）",
        "蚂蚁数字科技-IOT创新产品方案专家（AIoT方向）",
        "AI builder-策略方向",
        "Customer Experience Operation Specialist",
    ],
)
def test_remaining_functional_review_false_positives_are_excluded(title):
    result = classify_job_detailed(title, "负责 AI、安全平台和风险治理。")

    assert result.relevance_status == "excluded"
    assert result.tags == ()


def test_risk_strategy_specialist_is_not_rescued_by_domain_words():
    result = classify_job_detailed(
        "风控策略专员/高级专员（黑灰产对抗）",
        "负责黑灰产对抗、风险策略和用户分层。",
    )

    assert result.relevance_status == "excluded"
    assert result.tags == ()


def test_multimodal_model_research_requires_review():
    result = classify_job_detailed(
        "多模态理解与生成统一架构核心研究员-可灵AI",
        "负责多模态模型架构、训练和效果优化。",
    )

    assert result.relevance_status == "review"
    assert result.reasons == (REASON_ALGORITHM_ADJACENT_AI_REVIEW,)
