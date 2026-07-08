"""Configuration loading and validation for FindJobs sources."""

import re
from pathlib import Path
from typing import Any, List

import yaml
from pydantic import BaseModel, field_validator


_RE_HUAWEI = re.compile(r"huawei|华为", re.IGNORECASE)


def _reject_huawei(v: str) -> str:
    if _RE_HUAWEI.search(v):
        raise ValueError(
            f"Huawei references are not allowed in config: {v!r}"
        )
    return v


class CompanyConfig(BaseModel):
    """Configuration for a single tracked company."""

    slug: str
    name: str
    description: str = ""
    homepage_url: str = ""
    careers_url: str = ""

    @field_validator("slug", "name")
    @classmethod
    def no_huawei(cls, v: str) -> str:
        return _reject_huawei(v)


class SourceConfig(BaseModel):
    """Configuration for a single job data source."""

    slug: str
    name: str
    company_slug: str
    source_type: str = "official_careers"
    base_url: str = ""
    is_active: bool = False
    adapter: str = "generic_official"
    fetch_url: str = ""
    inactive_reason: str = ""
    parser_config: dict[str, Any] = {}

    @field_validator("slug", "name", "company_slug", "base_url", "fetch_url")
    @classmethod
    def no_huawei(cls, v: str) -> str:
        return _reject_huawei(v)


class SourcesConfig(BaseModel):
    """Top-level sources configuration."""

    companies: List[CompanyConfig] = []
    sources: List[SourceConfig] = []


def load_sources(path: Path | None = None) -> SourcesConfig:
    """Load and validate the sources YAML configuration.

    Args:
        path: Path to the YAML config file. Defaults to <config_dir>/sources.yaml.

    Returns:
        A validated SourcesConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If any company or source references Huawei.
    """
    if path is None:
        from findjobs.paths import get_config_dir

        path = get_config_dir() / "sources.yaml"

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    config = SourcesConfig.model_validate(data)
    return config
