"""Functions to sync company/source configuration into the database.

These helpers allow core tests to create Company and Source rows from
config objects without manually constructing ORM instances.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from findjobs.config import CompanyConfig, SourceConfig, SourcesConfig
from findjobs.models import Company, Source


def sync_company(session: Session, config: CompanyConfig) -> Company:
    """Ensure a company row exists matching *config*; return it.

    If a company with the same slug already exists its fields are
    updated in place.
    """
    existing: Company | None = (
        session.query(Company).filter(Company.slug == config.slug).first()
    )
    if existing is not None:
        existing.name = config.name
        existing.description = config.description
        existing.homepage_url = config.homepage_url
        existing.careers_url = config.careers_url
        return existing

    company = Company(
        slug=config.slug,
        name=config.name,
        description=config.description,
        homepage_url=config.homepage_url,
        careers_url=config.careers_url,
    )
    session.add(company)
    session.flush()
    return company


def _config_to_json(config: SourceConfig) -> str:
    """Serialize a SourceConfig as deterministic JSON for config_yaml."""
    return json.dumps(config.model_dump(), ensure_ascii=False, sort_keys=True, default=str)


def sync_source(session: Session, config: SourceConfig, company_id: int) -> Source:
    """Ensure a source row exists matching *config*; return it.

    If a source with the same slug + company_id already exists its
    fields are updated in place.
    """
    existing: Source | None = (
        session.query(Source)
        .filter(Source.slug == config.slug, Source.company_id == company_id)
        .first()
    )
    config_yaml = _config_to_json(config)

    if existing is not None:
        existing.name = config.name
        existing.source_type = config.source_type
        existing.base_url = config.base_url
        existing.is_active = config.is_active
        existing.config_yaml = config_yaml
        return existing

    source = Source(
        company_id=company_id,
        slug=config.slug,
        name=config.name,
        source_type=config.source_type,
        base_url=config.base_url,
        is_active=config.is_active,
        config_yaml=config_yaml,
    )
    session.add(source)
    session.flush()
    return source


def sync_config(session: Session, config: SourcesConfig) -> dict[str, dict[str, Any]]:
    """Sync all companies and sources from *config* into the database.

    Returns:
        A dict with keys ``"companies"`` and ``"sources"`` mapping
        config slugs to their ORM model instances::

            {
                "companies": {"tencent": <Company>},
                "sources":   {"tencent-careers": <Source>},
            }
    """
    company_map: dict[str, Company] = {}
    source_map: dict[str, Source] = {}

    for cc in config.companies:
        company = sync_company(session, cc)
        company_map[cc.slug] = company

    for sc in config.sources:
        company = company_map.get(sc.company_slug)
        if company is not None:
            source = sync_source(session, sc, company.id)
            source_map[sc.slug] = source

    session.flush()
    return {"companies": company_map, "sources": source_map}
