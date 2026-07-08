"""Shared target keyword constants for multi-keyword collection across adapters.

Adapters that currently use a single keyword (e.g. "安全") should iterate
these keywords to capture AI/Agent/MLOps/LLM/inference-deployment roles that
a single keyword would miss.

Classification is NOT part of this module; algorithm/security-algorithm
exclusion remains in ``findjobs.classify``.
"""

TARGET_KEYWORDS: list[str] = [
    "AI",
    "大模型",
    "智能体",
    "Agent",
    "LLM",
    "MLOps",
    "推理",
    "模型部署",
    "安全",
    "AI安全",
    "风控",
    "反作弊",
    "隐私",
    "数据安全",
    "云安全",
    "漏洞",
    "渗透",
    "攻防",
    "红队",
]
"""
Target keywords for AI, security, and infrastructure-adjacent role collection.

Includes AI-adjacent terms (AI, Agent, LLM, MLOps, 大模型, 智能体, 推理,
模型部署) and security terms (安全, AI安全, 风控, 反作弊, 隐私, 数据安全,
云安全, 漏洞, 渗透, 攻防, 红队).

Does not include 算法; algorithm and security-algorithm roles are excluded by
the classifier before persistence.
"""
