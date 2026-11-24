#!/bin/sh
# Linux (and macOS): run ./finassist.sh to open Coinleaf in your browser.
# The app runs only on this computer; press Ctrl+C here to stop it.
cd "$(dirname "$0")" || exit 1
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
if [ ! -x "$UV" ]; then
  echo "uv is not installed. Install it with:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "then run this again. See README.md -> Setup."
  exit 1
fi
"$UV" sync -q && exec "$UV" run coinleaf "$@"
