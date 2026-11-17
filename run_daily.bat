@echo off
REM TEDUH daily job for Windows Task Scheduler (fallback if GitHub Actions can't reach BMKG).
REM Jakarta first, then national for up to 240 minutes, then exit.
cd /d "%~dp0"
set PYTHONUTF8=1
call .venv\Scripts\activate.bat
echo [%date% %time%] daily job start >> run.out
python bmkg_collector.py --jakarta-once >> run.out 2>&1
python bmkg_collector.py --national-only --national-minutes 240 >> run.out 2>&1
echo [%date% %time%] daily job end >> run.out
