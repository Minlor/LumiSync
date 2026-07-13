#!/bin/bash
# LumiSync Development Setup Script
# This script builds and installs LumiSync in development/editable mode

set -e  # Exit on error

echo "=== LumiSync Development Setup ==="

# Check if we're in a virtual environment
if [ -z "$VIRTUAL_ENV" ]; then
    echo "Warning: No virtual environment detected. Consider activating one first."
fi

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build/ dist/ *.egg-info/

# Install in editable mode with dependencies
echo "Installing in editable mode..."
python -m pip install -e .

echo ""
echo "=== Setup Complete ==="
echo "You can now run 'lumisync' to start the application."