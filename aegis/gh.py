"""Thin GitHub helpers used by the fallback analyst and for issue lookup."""
from github import Github, Auth
from . import config

_client = None


def client() -> Github:
    global _client
    if _client is None:
        _client = Github(auth=Auth.Token(config.GITHUB_TOKEN)) if config.GITHUB_TOKEN else Github()
    return _client


def find_issue_by_marker(repo: str, marker: str) -> int | None:
    """Issues created by the Guild agent carry 'AEGIS-FP: <fingerprint>' in the body."""
    r = client().get_repo(repo)
    for iss in r.get_issues(state="all", labels=["aegis"]):
        if marker in (iss.body or ""):
            return iss.number
    return None


def create_issue(repo: str, title: str, body: str, labels=("aegis", "security")) -> int:
    r = client().get_repo(repo)
    for lb in labels:
        try:
            r.get_label(lb)
        except Exception:
            r.create_label(lb, "d73a4a" if lb == "security" else "0e8a16")
    return r.create_issue(title=title, body=body, labels=list(labels)).number


def close_issue(repo: str, number: int, comment: str) -> None:
    iss = client().get_repo(repo).get_issue(number)
    iss.create_comment(comment)
    iss.edit(state="closed")
