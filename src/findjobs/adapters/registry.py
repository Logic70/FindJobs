"""Adapter registry — maps names to adapter instances."""

from __future__ import annotations

from findjobs.adapters.base import BaseAdapter

_registry: dict[str, BaseAdapter] = {}


def register(name: str, adapter: BaseAdapter) -> None:
    """Register an adapter under *name*."""
    _registry[name] = adapter


def get_adapter(name: str) -> BaseAdapter:
    """Look up an adapter by name.

    Raises:
        ValueError: If *name* is not registered.
    """
    try:
        return _registry[name]
    except KeyError:
        raise ValueError(f"Unknown adapter: {name!r}")


def list_adapters() -> list[str]:
    """Return all registered adapter names."""
    return list(_registry.keys())
