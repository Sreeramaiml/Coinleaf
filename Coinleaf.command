#!/bin/zsh
# macOS: double-click in Finder to open Coinleaf in your browser.
# The app runs only on this Mac; close this window (or press Ctrl+C) to stop it.
cd "$(dirname "$0")" || exit 1
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
if [ ! -x "$UV" ]; then
  echo "uv is not installed. Install it with:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "then double-click this file again. See README.md → Setup."
  read -r "?Press Enter to close."; exit 1
fi
"$UV" sync -q && exec "$UV" run coinleaf
