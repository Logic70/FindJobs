"""Location splitting and normalization for UI filters."""

from __future__ import annotations

import re


_SEPARATOR_RE = re.compile(r"\s*(?:/|、|,|，|;|；|\n|\r)\s*")

_ALIASES = {
    "北京": "北京",
    "北京市": "北京",
    "beijing": "北京",
    "海淀": "北京",
    "海淀区": "北京",
    "上海": "上海",
    "上海市": "上海",
    "shanghai": "上海",
    "杭州": "杭州",
    "杭州市": "杭州",
    "hangzhou": "杭州",
    "拱墅": "杭州",
    "拱墅区": "杭州",
    "深圳": "深圳",
    "深圳市": "深圳",
    "shenzhen": "深圳",
    "南山": "深圳",
    "南山区": "深圳",
    "广东南山区": "深圳",
    "广东省南山区": "深圳",
    "广州": "广州",
    "广州市": "广州",
    "guangzhou": "广州",
    "成都": "成都",
    "成都市": "成都",
    "chengdu": "成都",
    "武汉": "武汉",
    "武汉市": "武汉",
    "wuhan": "武汉",
    "南京": "南京",
    "南京市": "南京",
    "nanjing": "南京",
    "合肥": "合肥",
    "合肥市": "合肥",
    "安徽合肥": "合肥",
    "安徽合肥市": "合肥",
    "安徽省合肥": "合肥",
    "安徽省合肥市": "合肥",
    "安徽省·合肥": "合肥",
    "安徽省·合肥市": "合肥",
    "苏州": "苏州",
    "苏州市": "苏州",
    "suzhou": "苏州",
    "西安": "西安",
    "西安市": "西安",
    "xian": "西安",
    "xi'an": "西安",
    "香港": "香港",
    "香港特别行政区": "香港",
    "中国香港": "香港",
    "hongkong": "香港",
    "hong kong": "香港",
    "澳门": "澳门",
    "澳门特别行政区": "澳门",
    "macao": "澳门",
    "macau": "澳门",
}

_CITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"北京|海淀"), "北京"),
    (re.compile(r"上海"), "上海"),
    (re.compile(r"杭州|拱墅"), "杭州"),
    (re.compile(r"深圳|南山"), "深圳"),
    (re.compile(r"广州"), "广州"),
    (re.compile(r"成都"), "成都"),
    (re.compile(r"武汉"), "武汉"),
    (re.compile(r"南京"), "南京"),
    (re.compile(r"合肥"), "合肥"),
    (re.compile(r"苏州"), "苏州"),
    (re.compile(r"西安"), "西安"),
    (re.compile(r"香港"), "香港"),
]

_EXTRACT_CITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"beijing|北京市?|海淀区?", re.IGNORECASE), "北京"),
    (re.compile(r"shanghai|上海市?", re.IGNORECASE), "上海"),
    (re.compile(r"hangzhou|杭州市?|拱墅区?", re.IGNORECASE), "杭州"),
    (re.compile(r"shenzhen|深圳市?|南山区?", re.IGNORECASE), "深圳"),
    (re.compile(r"guangzhou|广州市?", re.IGNORECASE), "广州"),
    (re.compile(r"chengdu|成都市?", re.IGNORECASE), "成都"),
    (re.compile(r"wuhan|武汉市?", re.IGNORECASE), "武汉"),
    (re.compile(r"nanjing|南京市?", re.IGNORECASE), "南京"),
    (re.compile(r"hefei|合肥市?", re.IGNORECASE), "合肥"),
    (re.compile(r"suzhou|苏州市?", re.IGNORECASE), "苏州"),
    (re.compile(r"xi'?an|xian|西安市?", re.IGNORECASE), "西安"),
    (re.compile(r"hongkong|hongkong|香港", re.IGNORECASE), "香港"),
    (re.compile(r"macao|macau|澳门", re.IGNORECASE), "澳门"),
]

_LOCATION_ORDER = {
    "北京": 10,
    "上海": 20,
    "深圳": 30,
    "杭州": 40,
    "广州": 50,
    "成都": 60,
    "武汉": 70,
    "南京": 80,
    "苏州": 90,
    "西安": 100,
    "合肥": 110,
    "香港": 900,
    "澳门": 910,
}


def _extract_known_locations(value: str) -> list[str]:
    """Extract multiple known cities from a separator-free source value."""
    text = re.sub(r"\s+", "", (value or "").strip())
    if not text:
        return []

    matches: list[tuple[int, str]] = []
    for pattern, city in _EXTRACT_CITY_PATTERNS:
        for match in pattern.finditer(text):
            matches.append((match.start(), city))

    results: list[str] = []
    for _, city in sorted(matches, key=lambda item: item[0]):
        if city not in results:
            results.append(city)
    return results


def normalize_location(value: str) -> str:
    """Normalize one location segment to a city filter value."""
    text = re.sub(r"\s+", "", (value or "").strip())
    if not text:
        return ""
    text = text.replace("·", "")
    text = (
        text.removesuffix("特别行政区")
        .removesuffix("省")
        .removesuffix("市")
    )
    alias = _ALIASES.get(text) or _ALIASES.get(text.lower())
    if alias:
        return alias
    for pattern, city in _CITY_PATTERNS:
        if pattern.search(text):
            return city
    return text


def split_locations(value: str) -> list[str]:
    """Split a possibly multi-location field into normalized city values."""
    results: list[str] = []
    for part in _SEPARATOR_RE.split(value or ""):
        extracted = _extract_known_locations(part)
        if len(extracted) > 1:
            for normalized in extracted:
                if normalized not in results:
                    results.append(normalized)
            continue

        normalized = normalize_location(part)
        if normalized and normalized not in results:
            results.append(normalized)
    return results


def format_locations(value: str) -> str:
    """Return a stable display/storage representation for location values."""
    locations = split_locations(value)
    locations.sort(key=lambda item: (_LOCATION_ORDER.get(item, 500), item))
    return "、".join(locations)


def location_matches(raw_location: str, selected_location: str) -> bool:
    """Return True when a raw multi-location field contains selected city."""
    selected = normalize_location(selected_location)
    return bool(selected) and selected in split_locations(raw_location)
