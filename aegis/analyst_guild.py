"""Preferred analyst: hand findings to the AEGIS analyst agent hosted on Guild.ai via an API trigger.
The Guild agent reads the code through Guild's GitHub integration and creates / closes the issue itself."""
import time
import httpx
from pathlib import Path
from . import config, gh

API = "https://api.guild.ai/v1"


def _auth():
    kid, _, secret = config.GUILD_TRIGGER_KEY.partition(":")
    return (kid, secret)


def start_session(agent_input: dict) -> dict:
    body = {"session_type": "api_trigger", "agent_input": agent_input}
    if config.GUILD_AGENT_ID:
        body["agent_id"] = config.GUILD_AGENT_ID
    r = httpx.post(f"{API}/workspaces/{config.GUILD_OWNER}/{config.GUILD_WORKSPACE}/sessions",
                   auth=_auth(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def wait(session_id: str, timeout: int = 240) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = httpx.get(f"{API}/sessions/{session_id}", auth=_auth(), timeout=30)
        status = r.json().get("root_task", {}).get("status", "")
        if status in ("DONE", "ERROR", "INTERRUPTED"):
            return status
        time.sleep(3)
    return "TIMEOUT"


def _context(workdir: Path, f: dict, radius: int = 25) -> str:
    try:
        lines = (workdir / f["path"]).read_text(errors="replace").splitlines()
    except OSError:
        return f["lines"]
    a = max(0, (f["start_line"] or 1) - 1 - radius)
    b = min(len(lines), (f["end_line"] or f["start_line"] or 1) + radius)
    return "\n".join(f"{i+1:4d}  {lines[i]}" for i in range(a, b))


def investigate(repo: str, sha: str, workdir: Path, f: dict, agent_name: str) -> dict:
    owner, name = repo.split("/")
    sess = start_session({
        "mode": "investigate", "agent_name": agent_name, "owner": owner, "repo": name, "commit": sha,
        "finding": {**f, "context": _context(workdir, f)},
    })
    status = wait(sess["id"])
    number = gh.find_issue_by_marker(repo, f"AEGIS-FP: {f['fingerprint']}")
    return {"filed": number is not None, "issue": number, "session": sess.get("session_url"), "status": status}


def verify_close(repo: str, sha: str, number: int, f: dict, agent_name: str) -> dict:
    owner, name = repo.split("/")
    sess = start_session({
        "mode": "verify", "agent_name": agent_name, "owner": owner, "repo": name, "commit": sha,
        "finding": f, "issue_number": number,
    })
    status = wait(sess["id"])
    return {"session": sess.get("session_url"), "status": status}
