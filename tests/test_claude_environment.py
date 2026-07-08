"""Claude Code environment diagnostics."""

from __future__ import annotations

from pathlib import Path


def test_claude_diagnostic_script_captures_cmd_and_powershell_resolution():
    script = Path("tools/diagnose_claude_env.ps1").read_text(encoding="utf-8")

    assert "where claude" in script
    assert "Get-Command claude.cmd" in script
    assert "claude.cmd --version" in script
    assert "npm prefix -g" in script
    assert "npm root -g" in script
    assert "PATH entries" in script


def test_claude_edit_smoke_script_uses_safe_mode_and_marker():
    script = Path("tools/run_claude_edit_smoke.ps1").read_text(encoding="utf-8")

    assert "--safe-mode" in script
    assert "--permission-mode bypassPermissions" in script
    assert "deepseek-v4-flash[1M]" in script
    assert "FINDJOBS_CLAUDE_EDIT_SMOKE_OK" in script
    assert ".claude-bypass-runs/edit-smoke/claude-edit-smoke.md" in script


def test_claude_status_doc_references_diagnostic_script():
    doc = Path("docs/claude-code-status.md").read_text(encoding="utf-8")

    assert "tools/diagnose_claude_env.ps1" in doc
    assert "tools/run_claude_edit_smoke.ps1" in doc
    assert "deepseek-v4-flash[1M]" in doc
    assert "FINDJOBS_CLAUDE_SMOKE_OK" in doc
    assert "FINDJOBS_CLAUDE_EDIT_SMOKE_OK" in doc
    assert "Failed Edit Attempts" in doc
    assert "Use Codex direct implementation" in doc
    assert "do not trust git diff alone" in doc
