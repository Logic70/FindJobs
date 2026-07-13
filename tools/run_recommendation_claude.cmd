@echo off
rem Run deterministic local weekly workflow first, then invoke Claude Code
rem for read-only narrative commentary over the factual outputs.
rem
rem Requires: uv, claude CLI in PATH.
rem No API key or base URL is stored in this repository -- configure via
rem environment variables (ANTHROPIC_API_KEY) or ~/.claude/settings.json.

echo === Step 1: Select profile ===

set "PROFILE_JSON=profile\profile.json"
set "PROFILE_MD=profile\profile.md"

set "PROFILE_FILE="
if exist "%PROFILE_JSON%" (
    set "PROFILE_FILE=%PROFILE_JSON%"
) else if exist "%PROFILE_MD%" (
    set "PROFILE_FILE=%PROFILE_MD%"
) else (
    echo Error: Neither profile\profile.json nor profile\profile.md found. 1>&2
    exit /b 1
)
echo Using profile: %PROFILE_FILE%

echo === Step 2: Deterministic local weekly workflow ===
CALL uv run findjobs weekly --live --profile "%PROFILE_FILE%"
if %errorlevel% neq 0 exit /b %errorlevel%

echo === Step 3: Verify expected outputs ===

set "JOBS_FILE=reports\match\jobs-full.jsonl"
set "RECS_FILE=reports\match\recommendations.json"
set "COMMENTARY_FILE=reports\match\claude-commentary.md"

if not exist "%JOBS_FILE%" (
    echo Error: %JOBS_FILE% not found. 1>&2
    exit /b 1
)
if not exist "%RECS_FILE%" (
    echo Error: %RECS_FILE% not found. 1>&2
    exit /b 1
)

echo === Step 4: Claude Code read-only commentary ===

set "GUARDRAIL_PROMPT=You are a career advisor analyzing job market data. Your inputs are: 1. reports/match/jobs-full.jsonl -- All exported job facts (full detail level) 2. reports/match/recommendations.json -- Deterministic scores, tiers, and evidence 3. %PROFILE_FILE% -- User profile. Guardrails (MANDATORY): - Consume exported facts only. Never fetch or invent jobs outside the data. - The recommendations.json scores and tiers are authoritative. Do NOT re-score, re-rank, re-order, or change facts. - Never estimate undisclosed salary. If salary_disclosed is false, treat salary as unknown. - Do not infer missing requirements. Missing requirements stay unknown. - Do not write to the database or any file. This is read-only commentary. - Clearly separate advice from official job facts. - Use the official URL for fact verification. - Add narrative commentary only -- no new data fields."

set "TEMP_COMMENTARY=%COMMENTARY_FILE%.tmp"

CALL claude --model "deepseek-v4-flash[1M]" --allowedTools "Read,Grep,Glob" --disallowedTools "Bash,Edit,Write" --print "%GUARDRAIL_PROMPT%" > "%TEMP_COMMENTARY%"
set "CLAUDE_EXIT=%errorlevel%"

if %CLAUDE_EXIT% neq 0 (
    echo Warning: Claude commentary step completed with exit code %CLAUDE_EXIT% 1>&2
    if exist "%TEMP_COMMENTARY%" del "%TEMP_COMMENTARY%"
    exit /b %CLAUDE_EXIT%
)

move /y "%TEMP_COMMENTARY%" "%COMMENTARY_FILE%" >nul
if %errorlevel% neq 0 (
    if exist "%TEMP_COMMENTARY%" del "%TEMP_COMMENTARY%"
    exit /b %errorlevel%
)

echo === Done ===
echo Deterministic outputs: reports/weekly/*, reports/match/jobs-full.jsonl, reports/match/recommendations.*
echo Claude commentary: reports/match/claude-commentary.md

exit /b 0
