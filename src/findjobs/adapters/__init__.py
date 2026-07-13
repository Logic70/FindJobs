"""Adapter package — official source job-data parsers.

Every adapter module in this package self-registers on import.
"""

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.registry import get_adapter, list_adapters, register

# Trigger adapter self-registration.
from findjobs.adapters import generic_official  # noqa: F401
from findjobs.adapters import tencent           # noqa: F401
from findjobs.adapters import alibaba           # noqa: F401
from findjobs.adapters import alibaba_group     # noqa: F401
from findjobs.adapters import antgroup          # noqa: F401
from findjobs.adapters import baidu             # noqa: F401
from findjobs.adapters import bytedance         # noqa: F401
from findjobs.adapters import deepseek          # noqa: F401
from findjobs.adapters import feishu            # noqa: F401
from findjobs.adapters import chaitin           # noqa: F401
from findjobs.adapters import jd                # noqa: F401
from findjobs.adapters import kuaishou          # noqa: F401
from findjobs.adapters import meituan           # noqa: F401
from findjobs.adapters import netease           # noqa: F401
from findjobs.adapters import iflytek           # noqa: F401
from findjobs.adapters import qianxin           # noqa: F401
from findjobs.adapters import sangfor           # noqa: F401

__all__ = [
    "AdapterContext",
    "BaseAdapter",
    "get_adapter",
    "list_adapters",
    "register",
]
