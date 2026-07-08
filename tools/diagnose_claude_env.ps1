param(
    [switch]$PromptSmoke
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Write-Section {
    param([string]$Title)
    Write-Output ""
    Write-Output "## $Title"
}

function Run-Cmd {
    param(
        [string]$Label,
        [string]$Command
    )
    Write-Output ""
    Write-Output "### $Label"
    Write-Output "Command: $Command"
    try {
        cmd /c $Command 2>&1
    } catch {
        Write-Output "ERROR: $($_.Exception.Message)"
    }
}

Write-Output "# Claude Code Environment Diagnostic"
Write-Output "Timestamp: $(Get-Date -Format o)"
Write-Output "PWD: $(Get-Location)"

Write-Section "PowerShell Resolution"
Run-Cmd "where claude from cmd" "where claude"
Write-Output ""
Write-Output "### Get-Command claude.cmd"
Get-Command claude.cmd -ErrorAction Continue | Format-List Source,Version,CommandType
Write-Output ""
Write-Output "### claude.cmd --version"
try {
    claude.cmd --version 2>&1
} catch {
    Write-Output "ERROR: $($_.Exception.Message)"
}

Write-Section "cmd Resolution"
Run-Cmd "cmd where claude" "where claude"
Run-Cmd "cmd claude version" "claude --version"

Write-Section "Node/NPM"
Run-Cmd "node version" "node --version"
Run-Cmd "where npm" "where npm"
Run-Cmd "npm prefix global" "npm prefix -g"
Run-Cmd "npm root global" "npm root -g"

Write-Section "Environment"
Write-Output "APPDATA=$env:APPDATA"
Write-Output "LOCALAPPDATA=$env:LOCALAPPDATA"
Write-Output "USERPROFILE=$env:USERPROFILE"
Write-Output "ComSpec=$env:ComSpec"
Write-Output "PATHEXT=$env:PATHEXT"
Write-Output ""
Write-Output "### PATH entries"
($env:Path -split ";") | Where-Object { $_ } | ForEach-Object { Write-Output $_ }

if ($PromptSmoke) {
    Write-Section "Non-Interactive Prompt Smoke"
    Write-Output "This smoke test must not edit files."
    try {
        claude.cmd `
            --model "deepseek-v4-flash[1M]" `
            --permission-mode bypassPermissions `
            -p "Print exactly: FINDJOBS_CLAUDE_SMOKE_OK" 2>&1
    } catch {
        Write-Output "ERROR: $($_.Exception.Message)"
    }
}
