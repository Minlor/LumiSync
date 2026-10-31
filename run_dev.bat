@echo off
setlocal

uv run --frozen --group dev python run_dev.py %*

endlocal
