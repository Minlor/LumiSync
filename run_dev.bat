@echo off
setlocal

REM Try to activate a common venv location if present
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
) else if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

python run_dev.py %*

endlocal
