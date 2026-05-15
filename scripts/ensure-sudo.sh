#!/usr/bin/env bash
# Ask for sudo once when first-time setup needs host-level checks.
set -euo pipefail

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required for first-time host setup and host preflight checks." >&2
  exit 1
fi

if sudo -n true >/dev/null 2>&1; then
  exit 0
fi

if [[ ! -t 0 ]]; then
  echo "sudo needs a password, but this shell is not interactive." >&2
  echo "Run the command from a terminal/SSH session with a TTY, for example:" >&2
  echo "  ssh -t nvidia@<spark-ip>" >&2
  echo "or configure passwordless sudo for unattended installs." >&2
  exit 1
fi

echo "This first-time setup needs sudo for host package checks and setup preflight."
echo "Enter the sudo password when prompted; passwordless sudo is not required."
sudo -v
