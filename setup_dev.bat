@echo off
REM LumiSync Development Setup Script
REM Installs the locked development environment with uv.

echo === LumiSync Development Setup ===

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is required. Install it from https://docs.astral.sh/uv/
    exit /b 1
)

REM Clean previous build output; the uv-managed .venv is kept and updated.
echo Cleaning previous builds...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.egg-info rmdir /s /q *.egg-info

echo Installing the locked environment...
uv sync --frozen --group dev

echo.
echo === Setup Complete ===
echo Run 'uv run lumisync' to start the application.
