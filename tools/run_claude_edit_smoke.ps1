param(
    [string]$Model = "deepseek-v4-flash[1M]",
    [switch]$UseWsl,
    [string]$WslClaudePath = "/home/zhde/.npm-packages/bin/claude"
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$smokeDir = Join-Path $root ".claude-bypass-runs\edit-smoke"
$targetRel = ".claude-bypass-runs/edit-smoke/claude-edit-smoke.md"
$target = Join-Path $root $targetRel
$outputLog = Join-Path $smokeDir "claude-output.log"
$expected = "# Claude Edit Smoke`n`nFINDJOBS_CLAUDE_EDIT_SMOKE_OK"

New-Item -ItemType Directory -Force -Path $smokeDir | Out-Null
Remove-Item -Force -ErrorAction SilentlyContinue $target

$prompt = @"
Create or overwrite exactly one file: $targetRel
The file content must be exactly:
# Claude Edit Smoke

FINDJOBS_CLAUDE_EDIT_SMOKE_OK

Do not edit any other file. Do not run shell commands. Reply exactly: DONE
"@

function Convert-ToWslPath {
    param([string]$Path)
    $resolved = Resolve-Path $Path
    $drive = $resolved.Path.Substring(0, 1).ToLowerInvariant()
    $rest = $resolved.Path.Substring(2).Replace("\", "/")
    return "/mnt/$drive$rest"
}

Write-Output "Running Claude edit smoke with model: $Model"
if ($UseWsl) {
    $wslRoot = Convert-ToWslPath $root
    $scriptPath = Join-Path $env:TEMP ("findjobs-claude-wsl-smoke-" + [guid]::NewGuid().ToString("N") + ".sh")
    $wslScript = @"
#!/usr/bin/env bash
set -euo pipefail
cd '$wslRoot'
CLAUDE_BIN='$WslClaudePath'
if [ ! -x "`$CLAUDE_BIN" ]; then
  echo "WSL Claude binary is not executable: `$CLAUDE_BIN" >&2
  exit 127
fi
"`$CLAUDE_BIN" \
  --safe-mode \
  --model '$Model' \
  --permission-mode bypassPermissions \
  --allowedTools 'Write,Edit,Read' \
  --disallowedTools 'Bash' \
  --output-format text \
  -p '$prompt'
"@
    [System.IO.File]::WriteAllText($scriptPath, $wslScript, [System.Text.UTF8Encoding]::new($false))
    $wslScriptPath = Convert-ToWslPath $scriptPath
    try {
        $claudeOutput = & wsl bash $wslScriptPath 2>&1
    } finally {
        Remove-Item -LiteralPath $scriptPath -Force -ErrorAction SilentlyContinue
    }
} else {
    $claudeOutput = & claude.cmd `
        --safe-mode `
        --model $Model `
        --permission-mode bypassPermissions `
        --allowedTools Write Edit Read `
        --disallowedTools Bash `
        --output-format text `
        -p $prompt 2>&1
}
$claudeExitCode = $LASTEXITCODE

$claudeOutput | Set-Content -Encoding UTF8 $outputLog

if ($claudeExitCode -ne 0) {
    Write-Error "Claude edit smoke failed: Claude exited with code $claudeExitCode. Output: $outputLog"
    exit 1
}

if (-not (Test-Path $target)) {
    Write-Error "Claude edit smoke failed: target file was not created. Output: $outputLog"
    exit 1
}

$actual = Get-Content -Raw -Encoding UTF8 $target
$actualNorm = $actual -replace "`r`n", "`n"
$actualComparable = $actualNorm -replace "`n$", ""
if ($actualComparable -ne $expected) {
    Write-Error (
        "Claude edit smoke failed: target content mismatch. " +
        "Expected marker FINDJOBS_CLAUDE_EDIT_SMOKE_OK. Output: $outputLog"
    )
    exit 1
}

Write-Output "Claude edit smoke passed: $targetRel"
