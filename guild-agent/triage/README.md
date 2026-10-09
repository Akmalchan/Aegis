# aegis-triage

Sub-agent of `aegis-sentinel-NN`. Takes one Semgrep finding, reads the file at the push commit through the GitHub
integration, and answers whether it is a true positive. Read-only: it never writes to GitHub and never records actions.

Input: `{repo, sha, agent, finding}` (finding = `Finding` from `openapi.yaml`, incl. optional `fix_hint` since v1.0.5).
Published: v1.0.5 `01a12252-7cd7-cf83-0000-b87356392702` (R4, 13:21). Real run 13:18: 4 parallel calls from the sentinel,
`repos_get_content` Unauthorized (no GitHub credential), judged from `lines` with confidence 0.8, valid JSON back.
Output (JSON text): `{confirmed, confidence, severity, cwe, title, impact, explanation, fix_suggestion}`.
Rubric: inline summary of `skills/security-review.md` (source -> sink -> consequence, CWE severity table, FP signals).

```bash
guild auth login                      # also pins @guildai-services registry in ~/.npmrc
cd guild-agent/triage && npm install && npx tsc --noEmit
guild agent init --name aegis-triage --template LLM --agent-type GUILD_TYPESCRIPT --directory .
guild agent save --message "triage v1" --wait --publish
echo '{"repo":"vincivv/snipbox","sha":"168f7e8e52c9f84163b72b6db9131fa7e48f70a3","agent":"aegis-sentinel-01","finding":{"rule_id":"aegis.sql-string-concat","path":"snipbox/db.py","start_line":40,"end_line":40,"lines":"cur.execute(\"SELECT * FROM snippets WHERE title LIKE \x27%\" + q + \"%\x27\")","message":"SQL built by string concatenation","severity":"ERROR","cwe":"CWE-89","fingerprint":"0123456789ab"}}' | guild agent test --mode json
```

Offline typecheck without the private registry: `npm run typecheck:offline` (uses `../_shims`).
