# AEGIS analyst (Guild.ai agent)

```bash
npm i -g @guildai/cli
guild auth login
cd guild-agent && npm install
guild workspace select
echo '{"mode":"investigate","agent_name":"aegis-01","owner":"andriidrok1","repo":"aegis-demo-target","commit":"main","finding":{"rule_id":"rules.aegis.sql-string-concat","path":"app.py","start_line":28,"end_line":28,"lines":"cur.execute(\"SELECT ... LIKE \x27%\" + q + \"%\x27\")","message":"SQL built by concatenation","severity":"ERROR","cwe":"CWE-89","fingerprint":"abc123def456"}}' | guild agent test --mode json
guild agent save --message "aegis analyst" --publish
```

Then in app.guild.ai: **Credentials → GitHub** (install the Guild GitHub app on the target repos), **Run → Triggers → Add Trigger → API** → copy `<id>:<secret>` into `.env` as `GUILD_TRIGGER_KEY` together with `GUILD_OWNER` / `GUILD_WORKSPACE`.
