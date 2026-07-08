"""Salary text parsing for Chinese tech job postings.

Supports common formats such as:
  - "30k-50k"           -> 30,000-50,000 CNY/month
  - "30K-50K·15薪"      -> 30,000-50,000 CNY/month (15-month bonus noted)
  - "2万-4万/月"        -> 20,000-40,000 CNY/month
  - "40-60万/年"        -> 400,000-600,000 CNY/year
  - "面议"               -> disclosed=True, no numeric values
  - None / ""           -> disclosed=False
"""

from __future__ import annotations

import re
from typing import Any


def parse_salary(salary_text: str | None) -> dict[str, Any]:
    """Parse a salary text string into structured salary data.

    Args:
        salary_text: Raw salary string from a job posting, or None.

    Returns:
        A dict with keys:
            salary_text:      The original text (or empty string).
            salary_min:       Parsed lower bound as a float, or None.
            salary_max:       Parsed upper bound as a float, or None.
            salary_currency:  ISO currency code (default "CNY").
            salary_period:    "monthly" or "yearly".
            salary_disclosed: Whether a numeric salary range was parsed.
    """
    result: dict[str, Any] = {
        "salary_text": salary_text or "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "CNY",
        "salary_period": "monthly",
        "salary_disclosed": False,
    }

    if not salary_text or not salary_text.strip():
        return result

    text = salary_text.strip()
    result["salary_text"] = text

    # 1) "30k-50k" or "30K-50K[·N薪]"
    m = re.match(
        r"^(\d+)\s*[kK]\s*[-–]\s*(\d+)\s*[kK](?:\s*[·‧]\s*\d+薪)?\s*$",
        text,
    )
    if m:
        result["salary_min"] = float(m.group(1)) * 1000
        result["salary_max"] = float(m.group(2)) * 1000
        result["salary_currency"] = "CNY"
        result["salary_period"] = "monthly"
        result["salary_disclosed"] = True
        return result

    # 2) "2万-4万/月"
    m = re.match(r"^(\d+)\s*万\s*[-–]\s*(\d+)\s*万\s*/\s*月$", text)
    if m:
        result["salary_min"] = float(m.group(1)) * 10000
        result["salary_max"] = float(m.group(2)) * 10000
        result["salary_currency"] = "CNY"
        result["salary_period"] = "monthly"
        result["salary_disclosed"] = True
        return result

    # 3) "40-60万/年"
    m = re.match(r"^(\d+)\s*[-–]\s*(\d+)\s*万\s*/\s*年$", text)
    if m:
        result["salary_min"] = float(m.group(1)) * 10000
        result["salary_max"] = float(m.group(2)) * 10000
        result["salary_currency"] = "CNY"
        result["salary_period"] = "yearly"
        result["salary_disclosed"] = True
        return result

    # Non-empty text but no numeric pattern matched (e.g. "面议")
    # Keep salary_disclosed=False since no numbers were parsed.
    return result
