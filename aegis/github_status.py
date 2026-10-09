"""Commit status setter. Guild's GitHub integration has no "create commit status" tool, so the scanner sets the status
itself (PyGithub, GITHUB_TOKEN) when an agent reports `status_set` to /actions, or calls POST /status directly."""
import logging
from fastapi import APIRouter
from pydantic import BaseModel
from . import gh, state

log = logging.getLogger("aegis.status")
router = APIRouter()
CONTEXT = "AEGIS / security-check"
STATES = ("success", "failure", "pending", "error")


def parse_ref(ref: str, state_field: str | None = None) -> tuple[str, str]:
    """ref = "<sha>:<state>" or "<sha>" (+ optional explicit state field). Unknown state -> success."""
    sha, _, st = (ref or "").partition(":")
    st = (state_field or st or "success").strip().lower()
    return sha.strip(), st if st in STATES else "success"


def description_for(n_findings: int | None, description: str | None = None) -> str:
    if description:
        return description[:140]
    if n_findings:
        return f"{n_findings} new finding(s)"
    return "no new findings"


def set_commit_status(repo: str, sha: str, state_: str, description: str = "", target_url: str = "") -> bool:
    """Never raises: logs and returns False on any failure."""
    try:
        kw = {"state": state_, "context": CONTEXT, "description": description_for(None, description)}
        if target_url:
            kw["target_url"] = target_url
        gh.client().get_repo(repo).get_commit(sha).create_status(**kw)
        state.log_event("status_set", repo=repo, sha=sha[:7], state=state_, description=kw["description"], via="scanner")
        return True
    except Exception as e:  # noqa
        log.warning("set_commit_status %s@%s %s failed: %s", repo, sha[:7], state_, e)
        state.log_event("error", repo=repo, stage="status_set", error=str(e)[:300])
        return False


def on_action(body) -> bool:
    """Hook for POST /actions with kind == status_set. Extra JSON fields (state, description) are read leniently."""
    extra = getattr(body, "model_extra", None) or {}
    sha, st = parse_ref(body.ref, extra.get("state"))
    if not sha:
        return False
    return set_commit_status(body.repo, sha, st, extra.get("description") or "", body.session_url)


class StatusIn(BaseModel):
    repo: str
    sha: str
    state: str = "success"
    description: str = ""
    target_url: str = ""
    agent: str = ""


@router.post("/status", operation_id="set_status",
             summary="Set the AEGIS commit status on a sha (success | failure | pending). The scanner does it via GitHub.")
def set_status(body: StatusIn):
    sha, st = parse_ref(body.sha, body.state)
    ok = set_commit_status(body.repo, sha, st, body.description, body.target_url)
    return {"ok": ok, "repo": body.repo, "sha": sha, "state": st, "context": CONTEXT}
