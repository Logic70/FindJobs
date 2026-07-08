@echo off
setlocal

rem Run from the repository root even when the script is started elsewhere.
cd /d "%~dp0.."

set "OPENCODE_EXE="
if exist "%APPDATA%\npm\opencode.cmd" set "OPENCODE_EXE=%APPDATA%\npm\opencode.cmd"
if "%OPENCODE_EXE%"=="" if exist "%APPDATA%\npm\node_modules\opencode-ai\bin\opencode.exe" set "OPENCODE_EXE=%APPDATA%\npm\node_modules\opencode-ai\bin\opencode.exe"
if "%OPENCODE_EXE%"=="" if exist "D:\Program Files (x86)\OpenCode\opencode.bat" set "OPENCODE_EXE=D:\Program Files (x86)\OpenCode\opencode.bat"
if "%OPENCODE_EXE%"=="" set "OPENCODE_EXE=opencode"

set "FINDJOBS_EXE=.venv\Scripts\findjobs.exe"
if not exist "%FINDJOBS_EXE%" set "FINDJOBS_EXE=findjobs"

echo Running deterministic local weekly workflow...
"%FINDJOBS_EXE%" weekly --no-live --reports-dir reports --profile profile\profile.md
if errorlevel 1 exit /b 1

echo Running opencode weekly workflow with the configured default model.
call "%OPENCODE_EXE%" run ^
  "Run the FindJobs weekly summary workflow for the attached export. Use only the attached exported facts. Do not invent jobs or salaries. Do not modify files or the database. Output only concise Chinese Markdown." ^
  --file workflows\weekly_summary.md ^
  --file reports\weekly\jobs.jsonl ^
  > reports\weekly\opencode-weekly-output.md 2>&1

if errorlevel 1 (
  echo opencode failed. Check reports\weekly\opencode-weekly-output.md and the opencode log directory.
  exit /b 1
)

echo Wrote reports\weekly\opencode-weekly-output.md
