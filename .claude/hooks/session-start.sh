#!/bin/bash
set -euo pipefail

# Load environment variables from .bashrc and .profile in a login shell context
bash -i -c '
  export DOTNET_ROOT=${HOME}/.dotnet
  export DOTNET_PATH=${HOME}/.dotnet
  export GODOT_HOME=${HOME}/Godot_Mono
  export GODOT_SHARP_DIR=${HOME}/Godot_Mono/Godot_v4.7.1-stable_mono_linux_x86_64/GodotSharp
  export GODOT_EXECUTABLE=${HOME}/Godot_Mono/Godot_v4.7.1-stable_mono_linux_x86_64/Godot_v4.7.1-stable_mono_linux.x86_64
  export PATH=${DOTNET_ROOT}:${DOTNET_ROOT}/tools:${PATH}
  export PATH=${HOME}/.local/bin:${PATH}
' > /dev/null 2>&1 || true

# Set environment variables for current session
export DOTNET_ROOT=${HOME}/.dotnet
export DOTNET_PATH=${HOME}/.dotnet
export GODOT_HOME=${HOME}/Godot_Mono
export GODOT_SHARP_DIR=${HOME}/Godot_Mono/Godot_v4.7.1-stable_mono_linux_x86_64/GodotSharp
export GODOT_EXECUTABLE=${HOME}/Godot_Mono/Godot_v4.7.1-stable_mono_linux_x86_64/Godot_v4.7.1-stable_mono_linux.x86_64
export PATH=${DOTNET_ROOT}:${DOTNET_ROOT}/tools:${PATH}

# Install Python development dependencies for linting and testing
pip install -q -r requirements-dev.txt

echo "✓ Environment setup complete: .NET/.Godot env vars set and dev dependencies installed"
