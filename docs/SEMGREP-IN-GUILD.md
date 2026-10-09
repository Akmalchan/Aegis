# Semgrep inside Guild: spike result (2026-10-09, 15:00-15:20)

Result: **does not work yet.** A Goose agent on Guild has a real shell, but our runtime-environment setup script never reached the session container, so `semgrep` is not there.

## What was built

- Runtime environment `andriidrok1~aegis-semgrep-env` (image `guildai~goosebox`, id `01a122aa-3658-beb3-0000-fc47f183ec85`). Setup: `pip install semgrep`, writes `/opt/aegis/rules.yml`, logs to `~/aegis-setup.log`, self-tests `semgrep scan`.
- Goose agent `andriidrok1~aegis-semgrep-native` (code in `guild-agent/aegis-semgrep-native/`, `environment:` set in guild.yaml), published, added to workspace `aegis`. `Validate runtime environment` passed on save.

## What happened

1. `guild runtime-environment test` returns `upstream request timeout` (HTTP 504 on `POST /sessions/{id}/events`) after ~60 s, even for a trivial env (`aegis-probe-env`, setup = `echo probe`). No setup log is ever shown.
2. `guild agent test` fails the same way (504). A chat session via `guild session create --prompt '{"text":..,"parameters":..}'` does run.
3. Inside that session Goose has tools `shell, write, edit, tree, recipe__final_output` and runs commands as `uid=1000(goose)`. Diagnostic output from the session:

```
PATH=/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games
ls: cannot access '/opt/aegis': No such file or directory
tail: cannot open '/home/goose/aegis-setup.log': No such file or directory
WARNING: Package(s) not found: semgrep
semgrep: command not found (exit 127)
```

Sessions finish in ~11 s, too fast for a pip install of semgrep, so the setup script most likely never ran for this session (or ran in another container).

## Next if someone picks it up

Ask at the Guild booth why env setup does not apply to Goose chat sessions and why `runtime-environment test` 504s. Runtime fallback was tried at 15:15 (recipe v4 forces `python3 -m pip install --user semgrep` as step 0, session `01a122bb-...`, see `guild session list`). The session sat in STARTED for 3+ minutes with no pip output by the 15:25 cutoff, which fits the docs: the container is offline after setup. The agent still has 4 published versions and the extra env `aegis-probe-env` exists; both are harmless.
