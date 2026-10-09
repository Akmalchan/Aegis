"""Fleet memory -> detection: annotate findings from ClickHouse and decide the verdict."""
from . import ch

BLOCKING = ("ERROR", "WARNING")


def apply(repo: str, findings: list[dict]) -> list[dict]:
    """Adds seen_before / dismissed_before / repo_mttr_h. Never fails the scan if ClickHouse is down."""
    try:
        ch.enrich(repo, findings)
    except Exception:  # noqa
        pass
    for f in findings:
        f.setdefault("seen_before", 0)
        f.setdefault("dismissed_before", False)
        f.setdefault("repo_mttr_h", 0.0)
    return findings


def verdict(findings: list[dict]) -> str:
    """unsafe if any ERROR/WARNING finding the fleet hasn't already dismissed as a false positive."""
    live = [f for f in findings if f.get("severity") in BLOCKING and not f.get("dismissed_before")]
    return "unsafe" if live else "safe"
