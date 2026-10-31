#!/bin/bash
# LumiSync Development Setup Script
# Installs the locked development environment with uv.

set -e  # Exit on error

echo "=== LumiSync Development Setup ==="

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required. Install it from https://docs.astral.sh/uv/"
    exit 1
fi

# Clean previous build output; the uv-managed .venv is kept and updated.
echo "Cleaning previous builds..."
rm -rf build/ dist/ *.egg-info/

echo "Installing the locked environment..."
uv sync --frozen --group dev

echo ""
echo "=== Setup Complete ==="
echo "Run 'uv run lumisync' to start the application."
