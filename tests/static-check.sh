#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

jq -e '.id == "sterling.youtube-music" and .barWidget.defaultSection == "right"' \
  "$repo_dir/manifest.json" >/dev/null

rg -q 'browser_bridge.py' "$repo_dir/Panel.qml"
rg -q 'backend.py' "$repo_dir/Panel.qml"
rg -q 'youtube_window_exists' "$repo_dir/scripts/browser_bridge.py"
rg -q 'fcntl.LOCK_EX' "$repo_dir/scripts/browser_bridge.py"
rg -q 'secret-tool' "$repo_dir/scripts/browser_bridge.py"
rg -q 'resultItems' "$repo_dir/Panel.qml"
rg -q 'searchTypeDropdown' "$repo_dir/Panel.qml"
rg -Fq 'Style.space(34)' "$repo_dir/Panel.qml"
rg -q 'api_request' "$repo_dir/scripts/backend.py"
rg -q 'FEmusic_liked_playlists' "$repo_dir/scripts/backend.py"
rg -q 'RUNTIME_COOKIES' "$repo_dir/scripts/backend.py"

echo "Static checks passed"
