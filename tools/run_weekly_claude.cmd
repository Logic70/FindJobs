@echo off
setlocal

rem Run from the repository root even when the script is started elsewhere.
cd /d "%~dp0.."

set "FINDJOBS_EXE=.venv\Scripts\findjobs.exe"
if not exist "%FINDJOBS_EXE%" set "FINDJOBS_EXE=findjobs"

echo Running deterministic local weekly workflow...
"%FINDJOBS_EXE%" weekly --no-live --reports-dir reports --profile profile\profile.md
if errorlevel 1 exit /b 1

echo Running claude weekly analysis workflow with deepseek-v4-flash[1M]...
echo This is a read-only analysis; no files or database will be modified by claude.
claude --model "deepseek-v4-flash[1M]" --permission-mode bypassPermissions --tools "Read,Grep,Glob" --disallowedTools "Bash,Edit,Write" --output-format text -p "You are a FindJobs weekly analysis assistant. Read workflows\weekly_summary.md and reports\weekly\jobs.jsonl from the current repository. Output concise Chinese Markdown. Use only facts from these exported files. Do not invent jobs or salaries. Do not estimate undisclosed salary. Do not modify files or the database; this is read-only analysis." > reports\weekly\claude-weekly-output.md 2>&1

if errorlevel 1 (
  echo claude failed. Check reports\weekly\claude-weekly-output.md for details.
  exit /b 1
)

echo Wrote reports\weekly\claude-weekly-output.md
