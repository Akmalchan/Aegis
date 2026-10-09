#!/usr/bin/env bash
# Start the scanner (port 8787) and a public tunnel for the GitHub webhook.
cd "$(dirname "$0")"
uv run uvicorn aegis.server:app --host 0.0.0.0 --port 8787 --reload &
sleep 2
cloudflared tunnel --url http://localhost:8787 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1 | tee state/tunnel_url.txt
wait
