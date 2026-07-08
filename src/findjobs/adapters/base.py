"""Base adapter protocol and context for official source collectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from findjobs.collection import CollectedJob


@dataclass
class AdapterContext:
    """Context passed to adapters during collection.

    Attributes:
        company_slug: Slug of the company this source belongs to.
        source_slug:  Slug of the source being collected.
        base_url:     Base career-page URL for the source.
        fetch_url:    Specific API endpoint for fetching jobs, if different
                      from *base_url*.
    """

    company_slug: str = ""
    source_slug: str = ""
    base_url: str = ""
    fetch_url: str = ""


class BaseAdapter:
    """Base class for source adapters with optional HTTP fetch helpers.

    Subclasses **must** implement :meth:`parse`.  The :meth:`fetch` and
    :meth:`collect` methods provide optional live-network support.
    """

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        """Parse a raw JSON response into a list of :class:`CollectedJob`.

        This is the primary interface for deterministic offline parsing.
        """
        raise NotImplementedError(  # pragma: no cover
            "Subclasses must implement parse()"
        )

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch raw JSON from the source URL using httpx.

        Override for custom HTTP logic (auth, pagination, etc.).
        """
        import httpx

        url = context.fetch_url or context.base_url
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def collect(
        self, context: AdapterContext
    ) -> list[CollectedJob]:
        """Fetch and parse in one call (live network collection)."""
        raw = self.fetch(context)
        return self.parse(raw, context)
