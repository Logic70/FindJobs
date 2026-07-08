# Claude Code Execution Status

## Current Decision

Follow-up development should prefer WSL Claude Code with
`deepseek-v4-flash[1M]` for small implementation tasks. Codex must still
validate the result and may fall back to direct implementation when Claude Code
does not modify files.

## Observed On 2026-07-07

- `claude.cmd --version` returned `2.1.195 (Claude Code)`.
- `where claude` resolved:
  - `C:\Users\zhd\AppData\Roaming\npm\claude`
  - `C:\Users\zhd\AppData\Roaming\npm\claude.cmd`
  - `C:\Users\zhd\AppData\Local\Microsoft\WindowsApps\claude.bat`
- `cmd /c where claude` resolves the same paths as PowerShell in the current
  Codex session.
- Global npm prefix/root are:
  - `C:\Users\zhd\AppData\Roaming\npm`
  - `C:\Users\zhd\AppData\Roaming\npm\node_modules`
- Non-interactive calls using `--model deepseek-v4-flash[1M] --permission-mode bypassPermissions -p`
  returned a project overview instead of editing files.
- A non-editing smoke prompt with `-PromptSmoke` returned
  `FINDJOBS_CLAUDE_SMOKE_OK`, so command resolution and basic non-interactive
  prompting work in the Codex PowerShell environment.
- A controlled editing smoke asked Claude Code to create only
  `docs/claude-edit-smoke.md` with marker `FINDJOBS_CLAUDE_EDIT_SMOKE_OK`.
  It again returned a project overview and did not create the file.
- Retrying the same edit smoke with `--safe-mode` bypassed the orientation
  response and did write a file, but the content was not the requested exact
  marker. A follow-up overwrite attempt then emptied the file. This means
  normal mode is likely affected by user settings/plugins, while safe mode still
  has instruction-fidelity issues for edit delegation.
- The guarded wrapper wrote run artifacts under `.claude-bypass-runs/`, but the
  project files are currently untracked, so git-diff-based target-change checks
  are not reliable until the repository is committed or files are added with
  intent-to-add.

## WSL Fallback Verified On 2026-07-07

- Default WSL distribution: `Ubuntu-22.04`.
- WSL Claude Code was updated from `2.1.148` to `2.1.202`, matching the latest
  npm registry version observed with `npm view @anthropic-ai/claude-code version`.
- WSL Node is `v20.20.1`. The current Claude Code package declares
  `node >=22.0.0`, but the installed native Claude binary starts and passes the
  smoke tests below.
- WSL user settings contain the same relevant DeepSeek API base URL and model
  environment variables as the Windows settings. Differences remain in
  non-essential defaults such as the top-level `model` value and missing
  `settings.local.json`/`CLAUDE.md` files in WSL.
- Non-login WSL shells can resolve the Windows `claude` shim first through the
  inherited Windows PATH. For repeatable delegation, call the explicit WSL
  binary path: `/home/zhde/.npm-packages/bin/claude`.
- A WSL non-edit prompt smoke returned `FINDJOBS_WSL_CLAUDE_OK`.
- A WSL edit smoke using `--safe-mode --model deepseek-v4-flash[1M]
  --permission-mode bypassPermissions` created the required disposable marker
  file under `.claude-bypass-runs/edit-smoke/`.

## Failed Edit Attempts

The same editing failure has now repeated three times:

1. Guarded wrapper task for classifier/location/type edits completed with no
   target-path diff and returned a project overview.
2. Direct `claude.cmd -p` implementation task for classifier/location/type
   edits returned a project overview and made no file changes.
3. Controlled edit smoke with only `Read,Write,Edit` tools allowed and `Bash`
   disallowed returned a project overview and did not create
   `docs/claude-edit-smoke.md`.

Likely cause: Claude Code command resolution and basic prompting work, but the
non-interactive editing session is being intercepted or routed into an
orientation-style response before tool execution. This is not a model-quality
issue in the project code itself.

## Repeatable Edit Smoke

Use this command to check whether edit delegation has become reliable:

```powershell
powershell -ExecutionPolicy Bypass -File tools/run_claude_edit_smoke.ps1
```

The script runs Claude Code with `--safe-mode`, asks it to write a disposable
file under `.claude-bypass-runs/edit-smoke/`, and fails unless the file contains
the required marker `FINDJOBS_CLAUDE_EDIT_SMOKE_OK`.

Current result on 2026-07-07: the script exits non-zero with a content mismatch,
so edit delegation is still not considered reliable.

Use this WSL-backed command for the working fallback:

```powershell
powershell -ExecutionPolicy Bypass -File tools/run_claude_edit_smoke.ps1 -UseWsl
```

Current WSL result on 2026-07-07: the script passes with
`FINDJOBS_CLAUDE_EDIT_SMOKE_OK`.

## Required Follow-Up

- Run `powershell -ExecutionPolicy Bypass -File tools/diagnose_claude_env.ps1`
  from the repository root to capture the PowerShell/cmd resolution of
  `claude`, Node/NPM paths, and relevant environment variables.
- Add `-PromptSmoke` to the diagnostic script only when a non-editing
  non-interactive Claude prompt smoke is desired.
- Prefer the WSL fallback for edit delegation until Windows edit smoke also
  passes.
- If using the wrapper while the repo is still fully untracked, add a semantic
  goal check or compare file hashes before/after; do not trust git diff alone.
- Do not switch back to opencode by default for development tasks unless the
  user explicitly asks for it.

## Operational Policy Until Fixed

- Use WSL Claude Code for delegated edits and keep the task package small.
- Use Codex direct implementation when Claude Code returns overview text without
  modifying files or fails the semantic goal check.
- Before retrying Windows editing delegation, compare Claude Code settings/hooks
  in the interactive `cmd` environment against this Codex PowerShell environment.
