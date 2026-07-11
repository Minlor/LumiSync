@echo off
REM LumiSync Development Setup Script
REM This script builds and installs LumiSync in development/editable mode

echo === LumiSync Development Setup ===

REM Check for virtual environment
if not defined VIRTUAL_ENV (
    echo Warning: No virtual environment detected. Consider activating one first.
)

REM Try to activate common venv locations
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
) else if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Clean previous builds
echo Cleaning previous builds...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.egg-info rmdir /s /q *.egg-info

REM Install in editable mode with dependencies
echo Installing in editable mode...
python -m pip install -e .

echo.
echo === Setup Complete ===
echo You can now run 'lumisync' to start the application.
