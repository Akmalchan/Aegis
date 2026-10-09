"""Second opinion from Semgrep's hosted MCP server (https://mcp.semgrep.ai/mcp, streamable HTTP, tool `semgrep_scan`).
POST /second-opinion {code, language} scans the snippet locally (bundled + learned + registry rules) and remotely, and
returns both plus the overlap by line. Hosted server down / needs auth / slow -> {available: false, reason}; the local
scan is still returned. Never raises."""
import asyncio, json, shutil, tempfile, time
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel, Field
from . import config, scanner

router = APIRouter()
MCP_URL = "https://mcp.semgrep.ai/mcp"
TIMEOUT_S = 45
EXT = {"python": ".py", "javascript": ".js", "typescript": ".ts", "go": ".go", "java": ".java", "ruby": ".rb",
       "php": ".php", "jsx": ".jsx", "tsx": ".tsx"}


class SecondOpinionIn(BaseModel):
    code: str = Field(description="source code to scan")
    language: str = Field(default="python", description="python | javascript | typescript | go | java | ruby | php")
    filename: str | None = Field(default=None, description="optional file name (extension picks the language)")


def _local(name: str, code: str) -> list[dict]:
    tmp = Path(tempfile.mkdtemp(prefix="aegis-2nd-", dir=str(config.CACHE_DIR)))
    try:
        (tmp / name).write_text(code)
        findings, _ = scanner.scan(tmp, [name])
        return [{k: f.get(k) for k in ("rule_id", "start_line", "severity", "message", "cwe")} for f in findings]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _remote(name: str, code: str) -> dict:
    from mcp import ClientSession
    try:
        from mcp.client.streamable_http import streamable_http_client as _client
    except ImportError:  # mcp < 2
        from mcp.client.streamable_http import streamablehttp_client as _client
    import os, httpx
    token = os.getenv("SEMGREP_MCP_TOKEN", "")  # OAuth bearer for mcp.semgrep.ai, if one was obtained
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    probe = httpx.post(MCP_URL, timeout=10, headers={**headers, "accept": "application/json, text/event-stream"},
                       json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                           "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "aegis", "version": "1"}}})
    if probe.status_code in (401, 403):
        return {"available": False, "reason": f"hosted Semgrep MCP needs OAuth ({probe.status_code} "
                f"{probe.headers.get('www-authenticate', '')[:120]}); set SEMGREP_MCP_TOKEN"}
    if probe.status_code >= 500:
        return {"available": False, "reason": f"hosted Semgrep MCP down (HTTP {probe.status_code})"}
    kw = {}
    if headers:
        try:
            import httpx2  # the mcp 2.x SDK takes its own httpx fork
            kw["http_client"] = httpx2.AsyncClient(headers=headers, timeout=30)
        except ImportError:
            kw["http_client"] = httpx.AsyncClient(headers=headers, timeout=30)
    async with _client(MCP_URL, **kw) as streams:
        async with ClientSession(streams[0], streams[1]) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            if "semgrep_scan" not in tools:
                return {"available": False, "reason": f"no semgrep_scan tool (has {sorted(tools)[:8]})"}
            props = (tools["semgrep_scan"].inputSchema or {}).get("properties", {})
            item = (props.get("code_files", {}).get("items", {}) or {}).get("properties", {}) or {}
            key = "filename" if "filename" in item else "path"
            res = await s.call_tool("semgrep_scan", {"code_files": [{key: name, "content": code}]})
            text = "\n".join(getattr(c, "text", "") for c in res.content)
            if res.isError:
                return {"available": False, "reason": text[:300] or "tool error"}
            data = getattr(res, "structuredContent", None) or {}
            if not data:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    return {"available": True, "raw": text[:2000], "findings": []}
            out = []
            for r in data.get("results", []):
                extra = r.get("extra", {}) or {}
                out.append({"rule_id": r.get("check_id", ""), "start_line": (r.get("start") or {}).get("line"),
                            "severity": extra.get("severity", ""), "message": (extra.get("message") or "")[:300]})
            return {"available": True, "findings": out, "errors": data.get("errors", [])[:3]}


@router.post("/second-opinion", operation_id="second_opinion",
             summary="Scan a code snippet locally AND with Semgrep's hosted MCP server (mcp.semgrep.ai, tool "
                     "semgrep_scan); return both and their overlap by line. available=false if the hosted server is down.")
def second_opinion(body: SecondOpinionIn):
    t0 = time.time()
    lang = body.language.lower()
    name = Path(body.filename).name if body.filename else "snippet" + EXT.get(lang, ".py")
    local = _local(name, body.code)
    try:
        remote = asyncio.run(asyncio.wait_for(_remote(name, body.code), TIMEOUT_S))
    except BaseException as e:  # noqa  (anyio ExceptionGroup, timeouts, HTTP 401/5xx)
        while getattr(e, "exceptions", None):  # unwrap anyio ExceptionGroups to the real cause
            e = e.exceptions[0]
        msg = f"{type(e).__name__}: {e}"
        remote = {"available": False, "reason": msg[:300]}
    rl = {f["start_line"] for f in remote.get("findings", [])}
    ll = {f["start_line"] for f in local}
    return {"language": lang, "filename": name, "local": local, "hosted": remote,
            "overlap_lines": sorted(x for x in rl & ll if x), "only_local_lines": sorted(x for x in ll - rl if x),
            "only_hosted_lines": sorted(x for x in rl - ll if x), "available": remote.get("available", False),
            "ms": int((time.time() - t0) * 1000)}
