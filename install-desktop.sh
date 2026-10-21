#!/bin/bash
# One-time setup on macOS: creates the venv, installs dependencies, and adds
# Frontline.app to ~/Applications. Then open it from Launchpad, Spotlight or the Dock.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
.venv/bin/python -m frontline.desktop --install
echo "Done - open Frontline from Launchpad or Spotlight (Cmd+Space, type Frontline)."
