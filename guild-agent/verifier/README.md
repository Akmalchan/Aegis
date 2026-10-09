# aegis-verifier

Sub-agent of `aegis-sentinel-NN`. Called when an open AEGIS issue's fingerprint is missing from the latest scan.
Comments `✅ Re-scanned <path> at <short sha>: <rule_id> no longer present. Closing. — AEGIS agent <agent>`,
closes the issue (`state: closed`, `state_reason: completed`), records `issue_closed` and calls
`aegis_scanner_set_status({repo, sha, state: "success", description: "<rule_id> no longer present in <path>", agent})`.

Input: `{repo, sha, agent, issue_number, fingerprint, path, rule_id}`. Output (JSON text): `{closed, issue_number, notes}`.
Tools: `github_issues_create_comment`, `github_issues_update`, `aegis_scanner_record_action`, `aegis_scanner_set_status`.
Published: v1.0.5 (R4 13:24, JSON-only output also on failure); v1.0.4 `01a1224a-bbd1-cf83-0000-317c1c1f29c7` (13:13, "W: verify loop").
Not exercised in the 13:18 real run (no open AEGIS issue could be read: GitHub credential not connected).

```bash
guild auth login
cd guild-agent/verifier && npm install && npx tsc --noEmit
guild agent init --name aegis-verifier --template LLM --agent-type GUILD_TYPESCRIPT --directory .
guild agent save --message "verifier v1" --wait --publish
echo '{"repo":"vincivv/snipbox","sha":"b7e2d9c4a1f0e3b6c5d8a9f2e1b4c7d0a3f6e9b2","agent":"aegis-sentinel-01","issue_number":1,"fingerprint":"0123456789ab","path":"snipbox/db.py","rule_id":"aegis.sql-string-concat"}' | guild agent test --mode json
```
Offline typecheck: `npm run typecheck:offline`.
