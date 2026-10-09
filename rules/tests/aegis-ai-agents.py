import os, subprocess, hmac, shlex
from fastapi import FastAPI, Request
from openai import OpenAI
from langchain.tools import tool
from mcp.server.fastmcp import FastMCP
app = FastAPI(); client = OpenAI(); mcp = FastMCP("x")

@app.post("/webhook")
async def hook(request: Request):
    # ruleid: aegis.webhook-missing-signature-check
    body = await request.json()
    return body

@app.post("/webhook2")
async def hook2(request: Request):
    sig = request.headers.get("x-sig")
    if not hmac.compare_digest(sig, "x"):
        return 401
    # ok: aegis.webhook-missing-signature-check
    body = await request.json()
    return body

def ask(q):
    key = os.environ["STRIPE_KEY"]
    # ruleid: aegis.taint-secret-into-prompt
    return client.chat.completions.create(model="m", messages=[{"role": "user", "content": f"use {key}: {q}"}])

@tool
def run_cmd(cmd: str):
    # ruleid: aegis.tool-arg-to-shell
    return subprocess.run(cmd, shell=True)

@mcp.tool()
def read_file(path: str):
    # ruleid: aegis.tool-arg-to-shell
    return open(path).read()

@tool
def safe(cmd: str):
    # ok: aegis.tool-arg-to-shell
    return subprocess.run("ls " + shlex.quote(cmd), shell=True)

@mcp.tool()
def run_shell(command: str) -> str:
    # ruleid: aegis.tool-arg-to-shell
    return subprocess.run(command, shell=True, capture_output=True, text=True).stdout

@mcp.tool(name="list_dir")
def list_dir(path: str):
    # ok: aegis.tool-arg-to-shell
    return subprocess.run(["ls", path]).stdout

def not_a_tool(cmd: str):
    # ok: aegis.tool-arg-to-shell
    return subprocess.run(cmd, shell=True)

def ask_ok(q):
    # ok: aegis.taint-secret-into-prompt
    return client.chat.completions.create(model="m", messages=[{"role": "user", "content": q}])
