"""Phase 2 tests: salary parser, classifier, collection persistence, and CLI --fixture.

All tests are deterministic and offline.  Chinese text is expressed as literal
characters (these tests run with UTF-8 source encoding).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# salary.py — parse_salary
# ---------------------------------------------------------------------------


class TestParseSalary:
    """parse_salary correctly handles disclosed, undisclosed, and edge cases."""

    def test_none_input(self):
        """None input should return salary_disclosed=False with None numeric fields."""
        from findjobs.salary import parse_salary

        result = parse_salary(None)
        assert result["salary_disclosed"] is False
        assert result["salary_min"] is None
        assert result["salary_max"] is None
        assert result["salary_text"] == ""

    def test_empty_string(self):
        """Empty string returns salary_disclosed=False."""
        from findjobs.salary import parse_salary

        result = parse_salary("")
        assert result["salary_disclosed"] is False
        assert result["salary_min"] is None
        assert result["salary_max"] is None
        assert result["salary_text"] == ""

    def test_whitespace_only(self):
        """Whitespace-only string returns salary_disclosed=False."""
        from findjobs.salary import parse_salary

        result = parse_salary("   ")
        assert result["salary_disclosed"] is False

    def test_k_format_lowercase(self):
        """'30k-50k' should parse as 30000-50000 monthly CNY."""
        from findjobs.salary import parse_salary

        result = parse_salary("30k-50k")
        assert result["salary_disclosed"] is True
        assert result["salary_min"] == 30000.0
        assert result["salary_max"] == 50000.0
        assert result["salary_currency"] == "CNY"
        assert result["salary_period"] == "monthly"
        assert result["salary_text"] == "30k-50k"

    def test_k_format_uppercase(self):
        """'30K-50K' should also parse correctly."""
        from findjobs.salary import parse_salary

        result = parse_salary("30K-50K")
        assert result["salary_disclosed"] is True
        assert result["salary_min"] == 30000.0
        assert result["salary_max"] == 50000.0

    def test_k_format_with_bonus(self):
        """'30K-50K·15薪' should parse as 30000-50000 monthly (bonus info preserved)."""
        from findjobs.salary import parse_salary

        result = parse_salary("30K-50K·15薪")
        assert result["salary_disclosed"] is True
        assert result["salary_min"] == 30000.0
        assert result["salary_max"] == 50000.0
        assert result["salary_currency"] == "CNY"
        assert result["salary_period"] == "monthly"

    def test_wan_format_monthly(self):
        """'2万-4万/月' should parse as 20000-40000 monthly."""
        from findjobs.salary import parse_salary

        result = parse_salary("2万-4万/月")
        assert result["salary_disclosed"] is True
        assert result["salary_min"] == 20000.0
        assert result["salary_max"] == 40000.0
        assert result["salary_period"] == "monthly"

    def test_wan_format_yearly(self):
        """'40-60万/年' should parse as 400000-600000 yearly."""
        from findjobs.salary import parse_salary

        result = parse_salary("40-60万/年")
        assert result["salary_disclosed"] is True
        assert result["salary_min"] == 400000.0
        assert result["salary_max"] == 600000.0
        assert result["salary_period"] == "yearly"

    def test_negotiable_text(self):
        """'面议' (negotiable) should have disclosed=False with numeric fields None."""
        from findjobs.salary import parse_salary

        result = parse_salary("面议")
        assert result["salary_disclosed"] is False
        assert result["salary_min"] is None
        assert result["salary_max"] is None
        assert result["salary_text"] == "面议"

    def test_unknown_format_preserves_text(self):
        """An unrecognised format keeps text but marks not disclosed."""
        from findjobs.salary import parse_salary

        result = parse_salary("competitive")
        assert result["salary_disclosed"] is False
        assert result["salary_min"] is None
        assert result["salary_max"] is None
        assert result["salary_text"] == "competitive"


# ---------------------------------------------------------------------------
# locations.py — normalize_location / split_locations
# ---------------------------------------------------------------------------


class TestLocationNormalization:
    """Location normalization correctly handles variants for filter display."""

    def test_beijing_district_values_collapse_to_beijing(self):
        """District-only Beijing values (海淀, 海淀区) collapse to 北京."""
        from findjobs.locations import normalize_location

        assert normalize_location("海淀") == "北京"
        assert normalize_location("海淀区") == "北京"
        assert normalize_location("北京") == "北京"

    def test_beijing_english_variant_collapses_to_beijing(self):
        """English 'Beijing' variant collapses to 北京."""
        from findjobs.locations import normalize_location

        assert normalize_location("Beijing") == "北京"

    def test_shenzhen_district_variant_collapses(self):
        """Shenzhen district variants (南山, 广东南山区) collapse to 深圳."""
        from findjobs.locations import normalize_location

        assert normalize_location("南山") == "深圳"
        assert normalize_location("广东南山区") == "深圳"
        assert normalize_location("深圳") == "深圳"

    def test_concatenated_city_values_split_to_individual_cities(self):
        """Concatenated official location fields should not become one filter value."""
        from findjobs.locations import split_locations

        assert split_locations("北京杭州") == ["北京", "杭州"]
        assert split_locations("北京上海深圳") == ["北京", "上海", "深圳"]
        assert split_locations("Beijing/Xi'an/西安市") == ["北京", "西安"]


# ---------------------------------------------------------------------------
# job_types.py — normalize_job_type / format_job_type
# ---------------------------------------------------------------------------


class TestJobTypeNormalization:
    """Job type normalization hides source codes and noisy source categories."""

    def test_source_codes_do_not_leak_to_filter_values(self):
        """J-code source values should map to a stable display category."""
        from findjobs.job_types import format_job_type

        assert format_job_type("J0012") == "技术"
        assert format_job_type("J0012") != "J0012"

    def test_common_engineering_subtypes_collapse_to_technical(self):
        """Backend/frontend/client/server categories collapse to 技术."""
        from findjobs.job_types import format_job_type

        assert format_job_type("后端") == "技术"
        assert format_job_type("前端") == "技术"
        assert format_job_type("客户端") == "技术"
        assert format_job_type("服务端") == "技术"
        assert format_job_type("大数据") == "技术"

    def test_machine_learning_categories_collapse_to_ai_engineering(self):
        """Machine-learning category text should not leak as a raw filter value."""
        from findjobs.job_types import format_job_type

        assert format_job_type("机器学习") == "AI工程"

    def test_noisy_business_and_industry_categories_collapse(self):
        """Broad source taxonomies should not appear as raw filter options."""
        from findjobs.job_types import format_job_type

        assert format_job_type("互联网、电子、网游") == "技术"
        assert format_job_type("商业分析、商业分析类") == "职能"
        assert format_job_type("战略与投资") == "职能"
        assert format_job_type("职能、支持") == "职能"
        assert format_job_type("产品、策划、项目") == "产品"
        assert format_job_type("物流、零售类") == "其他"


# ---------------------------------------------------------------------------
# classify.py — classify_job
# ---------------------------------------------------------------------------


class TestClassifyJob:
    """classify_job correctly tags AI, Security, and AI Security roles."""

    def test_ai_llm_signal(self):
        """Title with 'LLM' should receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("LLM Engineer", "Developing large language models")
        assert "AI" in tags
        assert "Security" not in tags

    def test_ai_damo_xing_signal(self):
        """Title with '大模型' should receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("大模型平台工程师", "")
        assert "AI" in tags

    def test_ai_aigc_signal(self):
        """AIGC in a description must not rescue a product-manager title."""
        from findjobs.classify import classify_job

        tags = classify_job("Product Manager", "AIGC content generation platform")
        assert tags == []

    def test_ai_product_manager_signal(self):
        """AI-only product manager roles are outside AI engineering scope."""
        from findjobs.classify import classify_job

        tags = classify_job("AI 产品经理", "负责 AI 产品体验与服务")
        assert tags == []

    def test_ai_mlops_signal(self):
        """Title with 'MLOps' should receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("MLOps Engineer", "CI/CD for ML pipelines")
        assert "AI" in tags

    def test_ai_inference_signal(self):
        """Description with 'inference' should receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("Backend Engineer", "Model inference optimization")
        assert "AI" in tags

    def test_ai_development_prefix(self):
        """Title starting with 'AI开发' should receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("AI开发工程师", "Building AI applications")
        assert "AI" in tags

    def test_ai_engineering_prefix(self):
        """Title 'AI工程' should receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("AI工程经理", "")
        assert "AI" in tags

    def test_ai_application_prefix(self):
        """Title 'AI应用' should receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("AI应用架构师", "")
        assert "AI" in tags

    def test_ai_agent_signal(self):
        """Agent and 智能体 application roles should receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("智能体终端安全工程师", "Agent security for AI assistants")
        assert "AI" in tags
        assert "Security" in tags
        assert "AI Security" in tags

    def test_ai_assistant_signal(self):
        """AI assistant product-manager roles are outside AI engineering scope."""
        from findjobs.classify import classify_job

        tags = classify_job("具身智能产品经理（人机协作及AI助手）", "")
        assert tags == []

    def test_ai_algorithm_prefix_excluded(self):
        """Title 'AI算法工程师' should NOT receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("AI算法工程师", "")
        assert "AI" not in tags
        assert "AI Security" not in tags

    def test_big_model_algorithm_title_excluded(self):
        """Title '大模型算法工程师' should NOT receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("大模型算法工程师", "LLM fine-tuning")
        assert "AI" not in tags
        assert "AI Security" not in tags

    def test_pure_algorithm_excluded(self):
        """Pure '算法工程师' without AI signals should NOT receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("算法工程师", "推荐系统开发")
        assert "AI" not in tags

    def test_pure_search_algorithm_excluded(self):
        """'搜索算法工程师' without AI signals should NOT receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("搜索算法工程师", "搜索引擎排序")
        assert "AI" not in tags

    def test_pure_recommendation_algorithm_excluded(self):
        """'推荐算法工程师' without AI signals should NOT receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("推荐算法工程师", "推荐系统")
        assert "AI" not in tags

    def test_security_appsec_signal(self):
        """Title 'AppSec Engineer' should receive Security tag."""
        from findjobs.classify import classify_job

        tags = classify_job("AppSec Engineer", "Application security")
        assert "Security" in tags

    def test_security_chinese(self):
        """Chinese security terms should trigger Security tag."""
        from findjobs.classify import classify_job

        tags = classify_job("安全工程师", "渗透测试与漏洞挖掘")
        assert "Security" in tags

    def test_general_safety_training_is_not_cybersecurity(self):
        """Operational safety roles should not be treated as security jobs."""
        from findjobs.classify import classify_job

        tags = classify_job("骑手安全培训专家", "负责配送安全培训和交通安全宣导")
        assert "Security" not in tags

    def test_traffic_safety_with_ai_tools_is_unrelated(self):
        """Traffic safety plus AI tooling language is not an AI/security role."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "交通安全专家",
            "职责: 借助AI完成联络记录归档。建立交通安全政策跟踪研判机制。",
            "公司事务/职能类",
        )
        assert tags == []

    def test_sales_preferred_ai_security_experience_is_unrelated(self):
        """Preferred AI/security background in requirements should not classify sales."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "销售中心-生态销售",
            (
                "岗位要求: 有数字内容风控、网络、信息安全相关业务经验者优先；"
                "对AI技术及AI商业应用领域发展趋势有持续关注者优先。"
                "岗位描述: 负责开拓客户及合作伙伴。"
            ),
            "销售",
        )
        assert tags == []

    def test_sales_of_ai_security_products_is_unrelated(self):
        """Sales management for AI/security product lines is not a target role."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "销售中心-东区销售负责人",
            (
                "岗位描述: 负责网易云信、易盾（内容安全）、AI 大模型 / 智能体等"
                "全线产品在华东区域的销售策略制定、业绩目标拆解与落地执行。"
            ),
            "销售",
        )
        assert tags == []

    def test_large_model_sales_title_is_unrelated(self):
        """Commercial sales roles are not target AI jobs even with large-model wording."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "\u5927\u6a21\u578b\u4e0e\u667a\u80fd\u4f53\u884c\u4e1a\u9500\u552e\u4e13\u5bb6\uff08\u987e\u95ee\u578b \u00b7 DRI\uff09",
            "\u804c\u8d23: \u8d1f\u8d23\u5ba2\u6237\u5f00\u62d3\u3001\u5546\u52a1\u8c08\u5224\u548c\u56de\u6b3e\u76ee\u6807\u3002",
            "\u9500\u552e\u4e13\u5458",
        )
        assert tags == []

    def test_large_model_market_sales_solution_role_is_unrelated(self):
        """Market/sales solution roles are outside AI engineering/platform scope."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "\u653f\u4f01\u5927\u6a21\u578b\u89e3\u51b3\u65b9\u6848\u5de5\u7a0b\u5e08",
            "\u804c\u8d23: \u5b8c\u6210\u5ba2\u6237\u6c9f\u901a\u3001\u65b9\u6848\u6f14\u793a\u548c\u552e\u524d\u652f\u6301\u3002",
            "\u5e02\u573a\u4e0e\u9500\u552e",
        )
        assert tags == []

    def test_ai_product_manager_remains_target_ai(self):
        """AI product roles are outside the engineering/platform/security scope."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "AI\u4ea7\u54c1\u7ecf\u7406 - \u6559\u80b2\u65b9\u5411",
            "\u804c\u8d23: \u8d1f\u8d23AI\u5e94\u7528\u4ea7\u54c1\u89c4\u5212\u548c\u843d\u5730\u3002",
            "\u4ea7\u54c1\u7ecf\u7406",
        )
        assert tags == []

    def test_large_model_product_operations_is_unrelated(self):
        """AI-adjacent product operations are outside target AI engineering scope."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "\u5927\u6a21\u578b\u4ea7\u54c1\u8fd0\u8425\u5b9e\u4e60\u751f",
            "\u804c\u8d23: \u8d1f\u8d23\u4ea7\u54c1\u8fd0\u8425\u3001\u5185\u5bb9\u7ef4\u62a4\u548c\u7528\u6237\u53cd\u9988\u3002",
            "\u5b9e\u4e60",
        )
        assert tags == []

    def test_llm_qa_role_is_unrelated(self):
        """QA/testing roles with LLM wording are not target AI roles."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "LLM \u5168\u94fe\u8def QA \u8d1f\u8d23\u4eba",
            "\u804c\u8d23: \u8d1f\u8d23\u6d4b\u8bd5\u6d41\u7a0b\u3001\u8d28\u91cf\u7ba1\u7406\u548c\u6548\u7387\u5efa\u8bbe\u3002",
            "\u7814\u53d1",
        )
        assert tags == []

    def test_security_operations_remains_security(self):
        """Security operations is still in scope for security jobs."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "\u5b89\u5168\u8fd0\u8425\u5de5\u7a0b\u5e08",
            "\u804c\u8d23: \u8d1f\u8d23\u6f0f\u6d1e\u54cd\u5e94\u3001\u98ce\u9669\u5904\u7f6e\u548c\u5b89\u5168\u5e73\u53f0\u8fd0\u8425\u3002",
            "\u5b89\u5168",
        )
        assert "Security" in tags

    def test_ai_transformation_consultant_is_unrelated(self):
        """AI transformation consulting is outside engineering/security scope."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "Consultant, AI Transformation",
            "职责: 负责企业AI转型咨询、客户访谈和变革管理。",
            "咨询、调研、顾问",
        )
        assert tags == []

    def test_game_server_security_basics_requirement_is_unrelated(self):
        """Security basics listed as a requirement should not classify server jobs."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "游戏服务器开发工程师",
            (
                "岗位要求: 具备计算机网络安全、编码安全的基本知识。"
                "岗位描述: 负责游戏服务器引擎开发和游戏逻辑开发。"
            ),
            "游戏程序",
        )
        assert tags == []

    def test_ai_tooling_for_generic_testing_is_unrelated(self):
        """Generic AI tooling for test efficiency is not an AI role."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "高级测试工程师",
            "岗位要求: 具备AI问题解决能力，能够运用AI工具解决测试质量和效率问题。",
            "技术",
        )
        assert tags == []

    def test_ai_coding_usage_is_not_ai_role(self):
        """Using AI coding tools in an ordinary client role is not an AI job."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "Keeta 客户端研发工程师",
            "职责: 推动 AI Coding 工作流落地，使用 Cursor、Copilot、Claude Code 提升研发效率。",
            "技术",
        )
        assert tags == []

    def test_ai_subsidy_operations_is_not_ai_role(self):
        """Business subsidy operations with AI wording are outside target AI roles."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "下沉市场-AI供给补贴运营",
            "职责: 构建商品智能补贴能力，使用 BI 工具做经营复盘。",
            "运营",
        )
        assert tags == []

    def test_ai_internal_control_is_not_ai_role(self):
        """Internal control roles mentioning AI governance are not AI engineering jobs."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "公司AI治理内控",
            "职责: 参与内控风险评估，访谈业务并编制底稿。",
            "职能",
        )
        assert tags == []

    def test_autonomous_sensor_ai_tooling_is_not_ai_role(self):
        """Autonomous-driving sensor work using AI tools is outside target AI scope."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "无人车业务部-自动驾驶传感器系统工程师",
            "职责: 负责传感器系统方案设计，利用 AI Agent 提升开发效率。",
            "技术",
        )
        assert tags == []

    def test_supply_chain_security_governance_is_not_cybersecurity(self):
        """Supplier governance safety is outside the target cyber/risk scope."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "腾讯游戏-安全运营高级工程师-供应链安全",
            "职责: 负责供应链安全管理平台的需求设计、数据治理、流程配置、报表看板和日常运营；建设AI工具清单。",
            "技术",
        )
        assert tags == []

    def test_warehouse_operations_safety_is_not_security(self):
        """Warehouse operational safety is not cybersecurity."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "仓经理（济南、青岛）储备",
            "职责: 负责仓储中心日常运营管理，确保仓储作业高效、安全、合规。",
            "物流/零售类",
        )
        assert tags == []

    def test_retail_safety_manager_is_not_security(self):
        """Retail/loss-prevention safety manager roles are out of scope."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "小象超市-区域安全经理",
            "职责: 负责服务站、配送的风险治理、消防、交通、冲突等各类事件处置和合规防损。",
            "门店/零售类",
        )
        assert tags == []

    def test_delivery_safety_manager_is_not_security(self):
        """Delivery safety management is not cybersecurity."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "北京安全经理",
            "职责: 负责配送业务安全事件预防，处理骑手交通事故和消防事件。",
            "职能",
        )
        assert tags == []

    def test_drone_business_safety_is_not_security(self):
        """Drone operation safety management is not cybersecurity."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "无人机-海外业务安全管理专家",
            "职责: 负责海外无人机业务安全管理、飞行安全和应急处置。",
            "运营",
        )
        assert tags == []

    def test_physical_security_manager_is_not_security(self):
        """Physical security and guard management are not target security jobs."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "物理安全经理 -【综效线-行政部】",
            "职责: 负责楼宇安全风险评估，管理安保服务供应商，保障办公场所安全。",
            "行政",
        )
        assert tags == []

    def test_autonomous_driving_safety_is_not_security(self):
        """Autonomous-driving safety is safety engineering, not cybersecurity."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "自动驾驶安全高级专家",
            "职责: 建立道路测试风险预防与响应体系，优化安全保障方案。",
            "综合",
        )
        assert tags == []

    def test_flight_safety_algorithm_is_not_security_or_ai(self):
        """Flight safety algorithm internships are excluded from AI/security scope."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "【实习】无人机-飞控算法实习生",
            "职责: 参与飞行安全策略设计、飞行控制律系统设计、算法开发测试落地。",
            "算法/技术类",
        )
        assert tags == []

    def test_waf_zero_trust_backend_keeps_security(self):
        """Explicit WAF/zero-trust engineering remains a target security role."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "Golang 研发工程师",
            "职责: 负责零信任、WAF 等安全系统控制面、日志流等模块的研发以及维护工作。",
            "技术",
        )
        assert "Security" in tags

    def test_account_security_keeps_security(self):
        """Account security remains a target security role."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "安全运营工程师-国际账号安全",
            "职责: 负责账号安全策略、风险监控和安全事件处置。",
            "研发类",
        )
        assert "Security" in tags

    def test_security_penetration(self):
        """'Penetration' description should trigger Security tag."""
        from findjobs.classify import classify_job

        tags = classify_job("Security Analyst", "Penetration testing")
        assert "Security" in tags

    def test_security_red_team(self):
        """'Red team' should trigger Security tag."""
        from findjobs.classify import classify_job

        tags = classify_job("Red Team Operator", "")
        assert "Security" in tags

    def test_security_data_security(self):
        """'Data security' should trigger Security tag."""
        from findjobs.classify import classify_job

        tags = classify_job("Data Security Manager", "")
        assert "Security" in tags

    def test_security_risk(self):
        """'Risk' should trigger Security tag."""
        from findjobs.classify import classify_job

        tags = classify_job("Risk Analyst", "fraud detection")
        assert "Security" in tags

    def test_ai_security_compound(self):
        """Job with both AI and Security signals should get all three tags."""
        from findjobs.classify import classify_job

        tags = classify_job("AI Security Engineer", "LLM security and red teaming")
        assert "AI" in tags
        assert "Security" in tags
        assert "AI Security" in tags

    def test_no_tags_for_unrelated(self):
        """A frontend engineer should get no tags."""
        from findjobs.classify import classify_job

        tags = classify_job("前端工程师", "React and TypeScript development")
        assert tags == []

    def test_algorithm_with_llm_excluded(self):
        """算法工程师 with LLM in description should NOT receive AI tag."""
        from findjobs.classify import classify_job

        tags = classify_job("算法工程师", "LLM fine-tuning and deployment")
        assert "AI" not in tags
        assert "AI Security" not in tags

    def test_security_algorithm_is_excluded(self):
        """Security algorithm roles are excluded from AI/security scope."""
        from findjobs.classify import classify_job

        tags = classify_job("资深反作弊算法工程师", "负责风控反作弊")
        assert tags == []

    def test_ai_security_algorithm_is_excluded(self):
        """AI-security algorithm roles are still algorithm roles and stay excluded."""
        from findjobs.classify import classify_job

        tags = classify_job("AI安全算法工程师", "负责AI安全算法", "算法")
        assert tags == []

    def test_security_direction_algorithm_is_excluded(self):
        """Algorithm roles marked as security direction are excluded."""
        from findjobs.classify import classify_job

        tags = classify_job("推荐算法工程师（安全方向）", "负责内容安全相关推荐算法优化")
        assert tags == []

    def test_algorithm_job_type_excluded_from_ai(self):
        """Job type containing 算法 should exclude AI even when title lacks it."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "LongCat大模型人才",
            "LLM safety and security platform work",
            "算法/技术类",
        )
        assert tags == []
        assert "AI" not in tags
        assert "AI Security" not in tags

    # ------------------------------------------------------------------ #
    # Phase-2 noise-reduction exclusions (AI-adjacent non-target roles)
    # ------------------------------------------------------------------ #

    def test_ui_designer_with_ai_is_not_target(self):
        """UI/UX designer mentioning AI direction is not a target AI role."""
        from findjobs.classify import classify_job

        tags = classify_job("UI设计师(AI方向)", "职责: 设计AI产品界面和交互体验")
        assert tags == []

    def test_content_editor_with_ai_is_not_target(self):
        """Content / editorial roles mentioning AI are not target AI roles."""
        from findjobs.classify import classify_job

        tags = classify_job("内容编辑(AI方向)", "职责: 负责AI相关内容的创作和编辑")
        assert tags == []

    def test_business_development_with_llm_is_not_target(self):
        """Business development roles with LLM wording are not target AI."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "业务拓展经理(AI大模型方向)",
            "职责: 开拓大模型业务市场和合作伙伴",
        )
        assert tags == []

    def test_ecosystem_partner_with_ai_is_not_target(self):
        """Ecosystem / partner roles mentioning AI are not target AI."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "AI大模型生态合作经理",
            "职责: 拓展AI生态合作伙伴和业务",
        )
        assert tags == []

    def test_customer_success_with_ai_is_not_target(self):
        """Customer / account success roles are not target AI."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "客户成功经理(AI方向)",
            "职责: 负责AI产品客户维护和续费",
        )
        assert tags == []

    def test_key_account_with_ai_is_not_target(self):
        """Key account / major customer roles are not target AI."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "AI大模型大客户总监",
            "职责: 负责大客户拓展和商务谈判",
        )
        assert tags == []

    def test_developer_community_with_ai_is_not_target(self):
        """Developer community operations roles are not target AI."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "开发者社区经理(AI方向)",
            "职责: 运营AI开发者社区和生态活动",
        )
        assert tags == []

    def test_generic_intern_with_ai_is_not_target(self):
        """Generic intern roles mentioning AI direction are not target AI."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "AI方向实习生",
            "职责: 协助AI产品团队完成日常任务",
        )
        assert tags == []

    def test_data_labeling_intern_with_llm_not_target(self):
        """Data labeling intern roles are not AI roles even when description mentions LLM."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "数据标注实习生",
            "职责: 负责LLM训练数据的标注和质量检查",
            "实习",
        )
        assert tags == []

    def test_ai_platform_intern_is_not_target(self):
        """Intern roles are not target AI roles for the job-search scope."""
        from findjobs.classify import classify_job

        tags = classify_job("AI平台实习生", "参与AI平台开发和运维")
        assert tags == []

    def test_ai_platform_engineer_remains_target(self):
        """Formal AI platform engineering roles remain target AI."""
        from findjobs.classify import classify_job

        tags = classify_job("AI平台工程师", "负责AI平台开发和MLOps运维")
        assert "AI" in tags

    def test_ai_product_operations_is_not_target(self):
        """AI product operations is not a target AI engineering/product role."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "AI产品海外游戏运营（AI+模拟经营方向）",
            "职责: 负责海外营销、社区增长、渠道投放和用户运营。",
        )
        assert tags == []

    def test_ai_agent_product_manager_is_not_target(self):
        """AI Agent product-manager titles are still non-engineering roles."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "AI Agent 产品经理",
            "职责: 负责智能体产品规划和商业化落地。",
            "产品",
        )
        assert tags == []

    def test_commercial_ai_product_manager_is_not_target(self):
        """Commercial AI product-manager roles should not enter target AI results."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "商业化系统AI产品经理",
            "职责: 负责商业化系统AI产品规划和需求管理。",
            "产品",
        )
        assert tags == []

    def test_patent_engineer_is_not_security(self):
        """Patent/IP legal roles are not cybersecurity roles."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "专利工程师",
            "职责: 制定大模型知识产权战略，处理专利无效、诉讼、谈判和合规风险。",
        )
        assert tags == []

    def test_customer_success_with_ai_security_is_not_target(self):
        """Customer-success roles are not target roles even with AI security wording."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "客户成功经理（AI安全方向）",
            "职责: 负责AI安全产品的客户对接和服务",
        )
        assert tags == []

    def test_security_operations_remains_security_after_noise_filters(self):
        """Security operations remains a target security role."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "隐私安全治理运营专家",
            "职责: 负责隐私保护、数据安全治理和安全运营。",
        )
        assert "Security" in tags

    def test_risk_control_operations_is_not_security_after_noise_filters(self):
        """Risk-control operations without engineering signals is not cybersecurity."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "广告风控运营专家",
            "职责: 负责反欺诈、风控策略和风险治理。",
        )
        assert tags == []

    def test_ai_technical_support_is_not_target(self):
        """Customer support / technical support is not target AI engineering."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "AI语音产品技术支持",
            "职责: 为客户提供AI语音产品使用支持和问题响应。",
        )
        assert tags == []

    def test_ai_recruiting_role_is_not_target(self):
        """Recruiting roles mentioning AI direction are not target AI roles."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "招聘专家（AI方向）",
            "职责: 负责AI团队招聘和候选人沟通。",
        )
        assert tags == []

    def test_llm_commercialization_manager_is_not_target(self):
        """Commercialization/business roles are not target AI roles."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "Overseas LLM Commercialization Manager",
            "Responsible for GTM, pricing, business planning, and partner growth.",
        )
        assert tags == []

    def test_finance_and_procurement_roles_are_not_security(self):
        """Finance/procurement roles should not be classified as security."""
        from findjobs.classify import classify_job

        assert classify_job("Channel Finance BP, France", "Risk reporting.") == []
        assert classify_job("Procurement Manager, Argentina", "Vendor risk review.") == []

    def test_ai_supplier_resource_role_is_not_target(self):
        """AI in a vendor/resource management title is not AI engineering."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "高级供应商管理工程师-AI资源",
            "职责: 负责AI算力资源供应商管理、合同与采购协同。",
        )
        assert tags == []

    def test_ai_teaching_research_role_is_not_target(self):
        """Teaching/curriculum roles from an AI org are outside target scope."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "AI研究院-教研员（语文\\英语)-合肥",
            "职责: 负责课程教研、教学内容建设和学习方案设计。",
        )
        assert tags == []

    def test_ai_medical_researcher_role_is_not_target(self):
        """Domain researcher titles should not match AI without engineering scope."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "中级AI医学研究员",
            "职责: 负责医学资料研究、临床专家沟通和知识整理。",
        )
        assert tags == []

    def test_ai_data_mining_role_is_not_target_without_security_surface(self):
        """Data mining is algorithm-adjacent unless a security surface is explicit."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "AI研究院-数据挖掘工程师",
            "职责: 负责数据挖掘、特征分析和业务洞察。",
        )
        assert tags == []

    # ---------------------------------------------------------------- #
    # Security false-positive guard: business risk / governance roles
    # ---------------------------------------------------------------- #

    def test_risk_control_strategist_is_not_security(self):
        """Risk control strategy roles are business functions, not cybersecurity."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "风控策略专家",
            "负责交易风险策略、用户分层、经营指标分析、策略效果复盘。",
        )
        assert "Security" not in tags

    def test_business_analyst_is_not_security(self):
        """Business operations analysis roles are not cybersecurity."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "经营分析师",
            "负责业务经营分析、数据看板、增长策略和预算测算。",
        )
        assert "Security" not in tags

    def test_risk_control_product_operations_is_not_security(self):
        """Risk control product operations roles are not cybersecurity."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "风控产品运营",
            "负责风控产品需求管理、运营流程、商家治理规则宣导。",
        )
        assert "Security" not in tags

    def test_asset_risk_management_is_not_security(self):
        """Asset risk/control management is a business governance role."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "\u57fa\u7840\u8bbe\u65bd\u8d44\u4ea7\u7ba1\u7406\u4e13\u5bb6-\u8d44\u4ea7\u5b89\u5168\u4e0e\u98ce\u63a7\u7ba1\u7406-\u676d\u5dde",
            "\u8d1f\u8d23\u8d44\u4ea7\u5185\u63a7\u5ba1\u8ba1\u3001\u4f9b\u5e94\u94fe\u98ce\u63a7\u3001\u7ecf\u8425\u5206\u6790\u548c\u6d41\u7a0b\u6cbb\u7406\u3002",
        )
        assert "Security" not in tags

    def test_anti_cheat_engineer_keeps_security(self):
        """Engineering anti-cheat roles should remain security."""
        from findjobs.classify import classify_job

        tags = classify_job(
            "反作弊工程师",
            "建设黑灰产识别、账号安全、风控系统和实时拦截平台。",
        )
        assert "Security" in tags


# ---------------------------------------------------------------------------
# collection.py — persistence (needs a real database)
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """Provide a fresh SQLite in-memory database session for each test."""
    from findjobs.db import init_db
    from sqlalchemy import inspect

    session = init_db(Path(tempfile.mktemp(suffix=".db")))
    yield session
    session.close()


@pytest.fixture
def demo_company_and_source(db_session):
    """Seed a minimal company + source, returning their ORM instances."""
    from findjobs.repository import sync_company, sync_source
    from findjobs.config import CompanyConfig, SourceConfig

    cc = CompanyConfig(slug="testcorp", name="Test Corp")
    company = sync_company(db_session, cc)

    sc = SourceConfig(
        slug="testcorp-careers",
        name="Test Corp Careers",
        company_slug="testcorp",
        source_type="official_careers",
        base_url="https://example.com",
        is_active=True,
    )
    source = sync_source(db_session, sc, company.id)

    db_session.commit()
    return company, source


@pytest.fixture
def sample_collected_jobs():
    """Return three CollectedJob instances for testing."""
    from findjobs.collection import CollectedJob

    return [
        CollectedJob(
            external_id="job-001",
            title="AI Engineer",
            url="https://example.com/jobs/001",
            description="LLM development",
            salary_text="30k-50k",
            salary_min=30000.0,
            salary_max=50000.0,
            salary_currency="CNY",
            salary_period="monthly",
            salary_disclosed=True,
            location="Beijing",
            job_type="full-time",
            matched_tags=["AI"],
        ),
        CollectedJob(
            external_id="job-002",
            title="Security Engineer",
            url="https://example.com/jobs/002",
            description="AppSec testing",
            salary_text="40-60万/年",
            salary_min=400000.0,
            salary_max=600000.0,
            salary_currency="CNY",
            salary_period="yearly",
            salary_disclosed=True,
            location="Beijing",
            job_type="full-time",
            matched_tags=["Security"],
        ),
        CollectedJob(
            external_id="job-003",
            title="AI Frontend Engineer",
            url="https://example.com/jobs/003",
            description="React UI development for AI assistant products",
            salary_text="",
            salary_disclosed=False,
            location="Shanghai",
            job_type="full-time",
            matched_tags=["AI"],
        ),
    ]


class TestCollectionPersistence:
    """Collection persistence: upsert, runs, observations, dedup."""

    def test_collect_jobs_creates_run_and_observations(
        self, db_session, demo_company_and_source, sample_collected_jobs
    ):
        """First collection creates one CollectRun and per-job observations."""
        from findjobs.collection import (
            collect_jobs,
            create_collect_run,
            complete_collect_run,
        )
        from findjobs.models import CollectRun, JobObservation

        company, source = demo_company_and_source

        run = create_collect_run(db_session, source.id)
        assert run.status == "running"

        total, new_count = collect_jobs(
            db_session, source.id, company.id, run.id, sample_collected_jobs
        )
        complete_collect_run(db_session, run, total, new_count)
        db_session.commit()

        assert total == 3
        assert new_count == 3
        assert run.status == "completed"
        assert run.jobs_found == 3
        assert run.jobs_new == 3

        # Verify observations created
        obs_count = (
            db_session.query(JobObservation)
            .filter(JobObservation.collect_run_id == run.id)
            .count()
        )
        assert obs_count == 3

    def test_repeated_collection_does_not_duplicate(
        self, db_session, demo_company_and_source, sample_collected_jobs
    ):
        """Second collection with same data should not duplicate jobs."""
        from findjobs.collection import (
            collect_jobs,
            create_collect_run,
            complete_collect_run,
        )
        from findjobs.models import CollectRun, Job, JobObservation

        company, source = demo_company_and_source

        # First run
        run1 = create_collect_run(db_session, source.id)
        total1, new1 = collect_jobs(
            db_session, source.id, company.id, run1.id, sample_collected_jobs
        )
        complete_collect_run(db_session, run1, total1, new1)
        db_session.commit()

        # Second run with same jobs
        run2 = create_collect_run(db_session, source.id)
        total2, new2 = collect_jobs(
            db_session, source.id, company.id, run2.id, sample_collected_jobs
        )
        complete_collect_run(db_session, run2, total2, new2)
        db_session.commit()

        assert total2 == 3
        assert new2 == 0  # No new jobs

        # Verify only 3 jobs total
        job_count = db_session.query(Job).filter(Job.source_id == source.id).count()
        assert job_count == 3

        # Verify 6 observations total (3 per run)
        obs_count = db_session.query(JobObservation).count()
        assert obs_count == 6

    def test_same_batch_duplicates_are_counted_once(
        self, db_session, demo_company_and_source
    ):
        """Duplicate jobs in one adapter result should produce one row and observation."""
        from findjobs.collection import CollectedJob, collect_jobs, create_collect_run
        from findjobs.models import Job, JobObservation

        company, source = demo_company_and_source
        duplicate_jobs = [
            CollectedJob(
                external_id="dup-001",
                title="Security Engineer",
                url="https://example.com/jobs/dup-001",
                location="Beijing",
                matched_tags=["Security"],
            ),
            CollectedJob(
                external_id="dup-001",
                title="Security Engineer Duplicate Page",
                url="https://example.com/jobs/dup-001?page=2",
                location="Beijing",
                matched_tags=["Security"],
            ),
        ]

        run = create_collect_run(db_session, source.id)
        total, new_count = collect_jobs(
            db_session, source.id, company.id, run.id, duplicate_jobs
        )
        db_session.commit()

        assert total == 1
        assert new_count == 1
        assert db_session.query(Job).filter(Job.source_id == source.id).count() == 1
        assert (
            db_session.query(JobObservation)
            .filter(JobObservation.collect_run_id == run.id)
            .count()
            == 1
        )

    def test_same_batch_duplicates_use_url_fallback(
        self, db_session, demo_company_and_source
    ):
        """Different external IDs with the same URL are one same-batch job."""
        from findjobs.collection import CollectedJob, collect_jobs, create_collect_run
        from findjobs.models import JobObservation

        company, source = demo_company_and_source
        duplicate_jobs = [
            CollectedJob(
                external_id="vendor-id-a",
                title="Security Engineer",
                url="https://example.com/jobs/same",
                location="Beijing",
                matched_tags=["Security"],
            ),
            CollectedJob(
                external_id="vendor-id-b",
                title="Security Engineer",
                url="https://example.com/jobs/same",
                location="Beijing",
                matched_tags=["Security"],
            ),
        ]

        run = create_collect_run(db_session, source.id)
        total, new_count = collect_jobs(
            db_session, source.id, company.id, run.id, duplicate_jobs
        )
        db_session.commit()

        assert total == 1
        assert new_count == 1
        assert (
            db_session.query(JobObservation)
            .filter(JobObservation.collect_run_id == run.id)
            .count()
            == 1
        )

    def test_irrelevant_jobs_are_not_persisted(
        self, db_session, demo_company_and_source
    ):
        """Jobs without AI/security tags should be skipped before persistence."""
        from findjobs.collection import CollectedJob, collect_jobs, create_collect_run
        from findjobs.models import Job

        company, source = demo_company_and_source
        jobs = [
            CollectedJob(
                external_id="irrelevant-001",
                title="Frontend Engineer",
                url="https://example.com/jobs/frontend",
                location="Beijing",
                matched_tags=[],
            ),
            CollectedJob(
                external_id="security-001",
                title="Security Engineer",
                url="https://example.com/jobs/security",
                location="Beijing",
                matched_tags=["Security"],
            ),
        ]

        run = create_collect_run(db_session, source.id)
        total, new_count = collect_jobs(db_session, source.id, company.id, run.id, jobs)
        db_session.commit()

        assert total == 1
        assert new_count == 1
        persisted = db_session.query(Job).filter(Job.source_id == source.id).all()
        assert [job.external_id for job in persisted] == ["security-001"]

    def test_collection_normalizes_location_and_job_type(
        self, db_session, demo_company_and_source
    ):
        """Stored jobs use stable location and job-type values for filters/export."""
        from findjobs.collection import CollectedJob, collect_jobs, create_collect_run
        from findjobs.models import Job

        company, source = demo_company_and_source
        jobs = [
            CollectedJob(
                external_id="normalized-001",
                title="Security Engineer",
                url="https://example.com/jobs/normalized",
                location="深圳市 / Beijing / 上海市",
                job_type="风险控制,综合支持/金融类",
                matched_tags=["Security"],
            )
        ]

        run = create_collect_run(db_session, source.id)
        collect_jobs(db_session, source.id, company.id, run.id, jobs)
        db_session.commit()

        persisted = db_session.query(Job).filter(Job.external_id == "normalized-001").one()
        assert persisted.location == "北京、上海、深圳"
        assert persisted.job_type == "风控、金融、职能"

    def test_collection_normalizes_noisy_multi_location_values(
        self, db_session, demo_company_and_source
    ):
        """Noisy official location fields should not create duplicate filter values."""
        from findjobs.collection import CollectedJob, collect_jobs, create_collect_run
        from findjobs.models import Job

        company, source = demo_company_and_source
        jobs = [
            CollectedJob(
                external_id="normalized-location-001",
                title="Security Engineer",
                url="https://example.com/jobs/normalized-location",
                location="中国香港、香港、广东南山区、安徽省·合肥市",
                matched_tags=["Security"],
            )
        ]

        run = create_collect_run(db_session, source.id)
        collect_jobs(db_session, source.id, company.id, run.id, jobs)
        db_session.commit()

        persisted = (
            db_session.query(Job)
            .filter(Job.external_id == "normalized-location-001")
            .one()
        )
        assert persisted.location == "深圳、合肥、香港"

    def test_prune_normalizes_existing_location_and_job_type(
        self, db_session, demo_company_and_source
    ):
        """Maintenance fixes old raw English/coded values already in the DB."""
        from findjobs.collection import CollectedJob, collect_jobs, create_collect_run
        from findjobs.maintenance import reclassify_and_prune_irrelevant_jobs
        from findjobs.models import Job

        company, source = demo_company_and_source
        run = create_collect_run(db_session, source.id)
        collect_jobs(
            db_session,
            source.id,
            company.id,
            run.id,
            [
                CollectedJob(
                    external_id="legacy-001",
                    title="Security Engineer",
                    url="https://example.com/jobs/legacy",
                    location="Beijing",
                    job_type="J0012",
                    description="AppSec",
                    matched_tags=["Security"],
                )
            ],
        )
        job = db_session.query(Job).filter(Job.external_id == "legacy-001").one()
        job.location = "Beijing"
        job.job_type = "J0012"
        db_session.commit()

        result = reclassify_and_prune_irrelevant_jobs(db_session)
        db_session.commit()

        refreshed = db_session.query(Job).filter(Job.external_id == "legacy-001").one()
        assert result.updated >= 2
        assert refreshed.location == "北京"
        assert refreshed.job_type == "技术"

    def test_repeated_collection_updates_last_seen_at(
        self, db_session, demo_company_and_source, sample_collected_jobs
    ):
        """Repeated collection should refresh last_seen_at and keep status=active."""
        from findjobs.collection import (
            collect_jobs,
            create_collect_run,
            complete_collect_run,
        )
        from findjobs.models import Job

        company, source = demo_company_and_source

        # First run
        run1 = create_collect_run(db_session, source.id)
        collect_jobs(db_session, source.id, company.id, run1.id, sample_collected_jobs)
        complete_collect_run(db_session, run1, 3, 3)
        db_session.commit()

        original_times = {
            j.external_id: j.last_seen_at
            for j in db_session.query(Job)
            .filter(Job.source_id == source.id)
            .all()
        }

        # Brief pause so timestamps differ
        time.sleep(0.01)

        # Second run
        run2 = create_collect_run(db_session, source.id)
        collect_jobs(db_session, source.id, company.id, run2.id, sample_collected_jobs)
        complete_collect_run(db_session, run2, 3, 0)

        # Manually set status to "archived" on one job, then re-collect should reset
        job1 = (
            db_session.query(Job)
            .filter(Job.external_id == "job-001")
            .first()
        )
        job1.status = "archived"
        db_session.commit()

        # Third run should reset status to active
        run3 = create_collect_run(db_session, source.id)
        collect_jobs(db_session, source.id, company.id, run3.id, [sample_collected_jobs[0]])
        complete_collect_run(db_session, run3, 1, 0)
        db_session.commit()

        # Verify last_seen_at updated
        refreshed = (
            db_session.query(Job)
            .filter(Job.external_id == "job-001")
            .first()
        )
        assert refreshed.last_seen_at > original_times["job-001"]
        assert refreshed.status == "active"

    def test_upsert_prefers_external_id(
        self, db_session, demo_company_and_source
    ):
        """When external_id matches, upsert uses it over title+location."""
        from findjobs.collection import CollectedJob, create_collect_run, collect_jobs
        from findjobs.models import Job

        company, source = demo_company_and_source

        job_a = CollectedJob(
            external_id="same-id",
            title="Security Engineer",
            url="https://example.com/a",
            location="Beijing",
            matched_tags=["Security"],
        )
        run1 = create_collect_run(db_session, source.id)
        collect_jobs(db_session, source.id, company.id, run1.id, [job_a])
        db_session.commit()

        job_b = CollectedJob(
            external_id="same-id",  # matches job_a
            title="Security Engineer - Updated",
            url="https://example.com/b",  # different URL
            location="Shanghai",  # different location
            matched_tags=["Security"],
        )
        run2 = create_collect_run(db_session, source.id)
        collect_jobs(db_session, source.id, company.id, run2.id, [job_b])
        db_session.commit()

        jobs = (
            db_session.query(Job)
            .filter(Job.source_id == source.id)
            .all()
        )
        assert len(jobs) == 1  # not duplicated
        assert jobs[0].title == "Security Engineer - Updated"


# ---------------------------------------------------------------------------
# repository.py — config sync
# ---------------------------------------------------------------------------


class TestRepository:
    """Repository config sync functions."""

    def test_sync_config_creates_company_and_source(self, db_session):
        """sync_config should create Company and Source from a SourcesConfig."""
        from findjobs.config import SourcesConfig, CompanyConfig, SourceConfig
        from findjobs.repository import sync_config
        from findjobs.models import Company, Source

        config = SourcesConfig(
            companies=[CompanyConfig(slug="acme", name="Acme Inc.")],
            sources=[
                SourceConfig(
                    slug="acme-careers",
                    name="Acme Careers",
                    company_slug="acme",
                    source_type="official_careers",
                    base_url="https://acme.com/careers",
                )
            ],
        )
        maps = sync_config(db_session, config)
        db_session.commit()

        assert "acme" in maps["companies"]
        assert "acme-careers" in maps["sources"]
        assert maps["companies"]["acme"].slug == "acme"
        assert maps["companies"]["acme"].name == "Acme Inc."

        company = (
            db_session.query(Company).filter(Company.slug == "acme").first()
        )
        assert company is not None

        source = (
            db_session.query(Source).filter(Source.slug == "acme-careers").first()
        )
        assert source is not None
        assert source.company_id == company.id


# ---------------------------------------------------------------------------
# CLI — collect --fixture
# ---------------------------------------------------------------------------


class TestCliFixture:
    """CLI collect --fixture integration."""

    def _make_fixture_file(self, tmpdir: str) -> Path:
        """Write a minimal fixture JSON and return its path."""
        data = {
            "company_slug": "testcorp",
            "source_slug": "testcorp-careers",
            "companies": [
                {
                    "slug": "testcorp",
                    "name": "Test Corp",
                    "description": "",
                    "homepage_url": "",
                    "careers_url": "",
                }
            ],
            "sources": [
                {
                    "slug": "testcorp-careers",
                    "name": "Test Corp Careers",
                    "company_slug": "testcorp",
                    "source_type": "official_careers",
                    "base_url": "https://example.com",
                    "is_active": True,
                }
            ],
            "jobs": [
                {
                    "external_id": "job-001",
                    "title": "AI Engineer",
                    "url": "https://example.com/jobs/001",
                    "description": "LLM development",
                    "salary_text": "30k-50k",
                    "location": "Beijing",
                    "job_type": "full-time",
                },
                {
                    "external_id": "job-002",
                    "title": "Security Engineer",
                    "url": "https://example.com/jobs/002",
                    "description": "AppSec testing",
                    "salary_text": "40-60万/年",
                    "location": "Beijing",
                    "job_type": "full-time",
                },
            ],
        }
        path = Path(tmpdir) / "fixture.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_cli_collect_fixture(self):
        """collect --fixture should persist jobs and print counts."""
        from findjobs.cli import app

        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = self._make_fixture_file(tmpdir)
            db_path = Path(tmpdir) / "test.db"

            result = runner.invoke(
                app,
                [
                    "collect",
                    "--fixture",
                    str(fixture_path),
                    "--db-path",
                    str(db_path),
                ],
            )
            assert result.exit_code == 0, f"CLI failed: {result.output}"
            assert "Fixture collection complete: 2 jobs, 2 new." in result.output

    def test_cli_collect_fixture_no_duplicates(self):
        """Running collect --fixture twice should not duplicate jobs."""
        from findjobs.cli import app

        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = self._make_fixture_file(tmpdir)
            db_path = Path(tmpdir) / "test.db"

            # First run
            result1 = runner.invoke(
                app, ["collect", "--fixture", str(fixture_path), "--db-path", str(db_path)]
            )
            assert result1.exit_code == 0
            assert "2 new" in result1.output

            # Second run
            result2 = runner.invoke(
                app, ["collect", "--fixture", str(fixture_path), "--db-path", str(db_path)]
            )
            assert result2.exit_code == 0
            assert "0 new" in result2.output

    def test_cli_collect_without_fixture_shows_message(self):
        """collect without --fixture should list active sources and require --live."""
        from findjobs.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["collect"])
        assert result.exit_code == 0
        assert "active source(s) configured" in result.output
        assert "Use --live to collect" in result.output
