"""Adapter quality gates for official-source collection."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner


def test_active_source_adapters_have_quality_gates():
    """Every active source must have machine-readable quality evidence."""
    from findjobs.adapters.quality import ADAPTER_QUALITY_GATES
    from findjobs.config import load_sources

    config = load_sources()
    active_adapters = {source.adapter for source in config.sources if source.is_active}

    assert active_adapters <= set(ADAPTER_QUALITY_GATES)


def test_quality_gate_fixture_files_exist():
    """Each quality gate points to an offline adapter fixture."""
    from findjobs.adapters.quality import ADAPTER_QUALITY_GATES

    fixtures_dir = Path("tests/fixtures/adapters")

    for gate in ADAPTER_QUALITY_GATES.values():
        assert (fixtures_dir / gate.fixture).exists(), gate.adapter


def test_quality_gate_offline_tests_are_real_names():
    """Gate metadata should reference actual deterministic tests."""
    from findjobs.adapters.quality import ADAPTER_QUALITY_GATES

    test_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path("tests/test_phase3.py"), Path("tests/test_phase3_new_sources.py"))
    )

    for gate in ADAPTER_QUALITY_GATES.values():
        for test_name in gate.offline_tests:
            assert test_name in test_text, f"{gate.adapter}: {test_name}"


def test_quality_gates_include_required_acceptance_items():
    """Codify adapter review items learned from prior collection failures."""
    from findjobs.adapters.quality import ADAPTER_QUALITY_GATES

    for gate in ADAPTER_QUALITY_GATES.values():
        assert gate.official_source is True, gate.adapter
        assert gate.salary_facts_only is True, gate.adapter
        assert gate.target_relevance_filtering is True, gate.adapter
        assert gate.algorithm_exclusion is True, gate.adapter
        assert gate.stable_identity is True, gate.adapter
        assert gate.field_normalization is True, gate.adapter
        assert gate.pagination, gate.adapter
        assert gate.deduplication, gate.adapter
        assert gate.detail_enrichment, gate.adapter
        assert gate.live_smoke, gate.adapter


def test_tencent_and_meituan_require_detail_enrichment_for_requirements():
    """Requirement loss in Tencent/Meituan must stay an explicit gate."""
    from findjobs.adapters.quality import ADAPTER_QUALITY_GATES

    tencent = ADAPTER_QUALITY_GATES["tencent_official"]
    meituan = ADAPTER_QUALITY_GATES["meituan_official"]

    assert "Requirement" in tencent.detail_enrichment
    assert "jobRequirement" in meituan.detail_enrichment
    assert "test_collect_fetches_detail_to_fill_requirements" in meituan.offline_tests


def test_adapter_audit_cli_reports_quality_for_active_sources():
    """The CLI exposes adapter evidence for Codex review and user diagnosis."""
    from findjobs.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["adapter-audit"])

    assert result.exit_code == 0, result.output
    assert "tencent-careers" in result.output
    assert "quality=ok" in result.output
    assert "fixture=tencent.json" in result.output
    assert "algorithm_exclusion" in result.output


def test_adapter_audit_all_includes_limited_inactive_sources():
    """Inactive backlog adapters are visible but marked limited."""
    from findjobs.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["adapter-audit", "--all"])

    assert result.exit_code == 0, result.output
    assert "alibaba-talent" in result.output
    assert "quality=limited" in result.output
