# aegis-verifier

Sub-agent of `aegis-sentinel-NN`. Called when an open AEGIS issue's fingerprint is missing from the latest scan.
Comments `✅ Re-scanned <path> at <short sha>: <rule_id> no longer present. Closing. — AEGIS agent <agent>`,
closes the issue (`state: closed`, `state_reason: completed`) and records `issue_closed`.

Input: `{repo, sha, agent, issue_number, fingerprint, path, rule_id}`. Output (JSON text): `{closed, issue_number, notes}`.
Tools: `github_issues_create_comment`, `github_issues_update`, `aegis_scanner_record_action`.

```bash
guild auth login
cd guild-agent/verifier && npm install && npx tsc --noEmit
guild agent init --name aegis-verifier --template LLM --agent-type GUILD_TYPESCRIPT --directory .
guild agent save --message "verifier v1" --wait --publish
echo '{"repo":"vincivv/snipbox","sha":"b7e2d9c4a1f0e3b6c5d8a9f2e1b4c7d0a3f6e9b2","agent":"aegis-sentinel-01","issue_number":1,"fingerprint":"0123456789ab","path":"snipbox/db.py","rule_id":"aegis.sql-string-concat"}' | guild agent test --mode json
```
Offline typecheck: `npm run typecheck:offline`.
