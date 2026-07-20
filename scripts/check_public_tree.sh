#!/usr/bin/env bash
set -euo pipefail

scan_root="${1:-.}"
legacy_one='Stock''God'
legacy_two='tiger''_research_copilot'
legacy_three='TIGER''_COPILOT_'
personal_user='robbie''mac'
pattern="${legacy_one}|${legacy_two}|${legacy_three}|/Users/|${personal_user}"

if rg -n -i --hidden --glob '!.git/**' --glob '!scripts/check_public_tree.sh' "$pattern" "$scan_root"; then
  echo "Public-tree identity scan failed." >&2
  exit 1
fi

echo "Public-tree identity scan passed."
