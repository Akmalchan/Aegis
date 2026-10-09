#!/usr/bin/env bash
# PostToolUse hook (Edit|Write|MultiEdit): Semgrep scans the file the coding agent just wrote.
# Logged in to Semgrep (SEMGREP_APP_TOKEN or `semgrep login`): Semgrep's own hook, `semgrep mcp -k post-tool-cli-scan -a claude`.
# Not logged in: the same idea offline, with this repo's rules/ on the edited file. Exit 2 = stderr goes back to Claude.
input=$(cat)
if [ -n "$SEMGREP_APP_TOKEN" ] || grep -q '^api_token:' "$HOME/.semgrep/settings.yml" 2>/dev/null; then
  printf '%s' "$input" | exec semgrep mcp -k post-tool-cli-scan -a claude
fi
file=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')
[ -f "$file" ] || exit 0
root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
out=$(semgrep scan --quiet --metrics=off --error --config "$root/rules" "$file" 2>/dev/null)
rc=$?
if [ "$rc" -eq 1 ]; then
  printf 'Semgrep (AEGIS rules) found issues in %s, fix them before continuing:\n%s\n' "$file" "$out" >&2
  exit 2
fi
exit 0
