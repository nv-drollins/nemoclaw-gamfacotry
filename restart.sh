#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --delete-sandbox|--remove-sandbox)
      ;;
    *)
      SETUP_ARGS+=("$arg")
      ;;
  esac
done

"$SCRIPT_DIR/stop.sh" "$@"
"$SCRIPT_DIR/scripts/setup_nemoclaw_app_factory.sh" "${SETUP_ARGS[@]}"
