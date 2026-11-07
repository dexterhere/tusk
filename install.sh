#!/bin/sh
# Tusk bootstrap: clone (or update) into ~/.local/share/tusk, then run `tusk setup`.
# Read it first if you like: https://github.com/dexterhere/tusk/blob/main/install.sh
set -eu
DIR="${TUSK_DIR:-$HOME/.local/share/tusk}"
command -v git >/dev/null 2>&1 || { echo "git is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone --depth 1 https://github.com/dexterhere/tusk.git "$DIR"; fi
"$DIR/bin/tusk" doctor || true
"$DIR/bin/tusk" install
echo
echo "Then run /tusk-full in Claude Code inside the project you want to test."
