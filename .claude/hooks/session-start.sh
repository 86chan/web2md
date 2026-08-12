#!/bin/bash
set -euo pipefail

# Only run in Claude Code on the web (cloud environment)
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Set .NET and Godot environment variables in CLAUDE_ENV_FILE if available
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  cat >> "$CLAUDE_ENV_FILE" << 'ENVEOF'
export DOTNET_ROOT=${HOME}/.dotnet
export DOTNET_PATH=${HOME}/.dotnet
export GODOT_HOME=${HOME}/Godot_Mono
export GODOT_SHARP_DIR=${HOME}/Godot_Mono/Godot_v4.7.1-stable_mono_linux_x86_64/GodotSharp
export GODOT_EXECUTABLE=${HOME}/Godot_Mono/Godot_v4.7.1-stable_mono_linux_x86_64/Godot_v4.7.1-stable_mono_linux.x86_64
export PATH=${DOTNET_ROOT}:${DOTNET_ROOT}/tools:${PATH}
ENVEOF
  # Source the env file to apply variables in current session
  source "$CLAUDE_ENV_FILE"
fi

# Also set in current session
export DOTNET_ROOT=${HOME}/.dotnet
export DOTNET_PATH=${HOME}/.dotnet
export GODOT_HOME=${HOME}/Godot_Mono
export GODOT_SHARP_DIR=${HOME}/Godot_Mono/Godot_v4.7.1-stable_mono_linux_x86_64/GodotSharp
export GODOT_EXECUTABLE=${HOME}/Godot_Mono/Godot_v4.7.1-stable_mono_linux_x86_64/Godot_v4.7.1-stable_mono_linux.x86_64
export PATH=${DOTNET_ROOT}:${DOTNET_ROOT}/tools:${PATH}

# Install Python development dependencies for linting and testing
pip install -q -r requirements-dev.txt

echo "✓ Environment setup complete: .NET/.Godot env vars configured and dev dependencies installed"
