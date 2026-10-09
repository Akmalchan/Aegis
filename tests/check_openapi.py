"""Contract 1 guard: the routes FastAPI serves must match openapi.yaml (what Guild imports).
Run: uv run python tests/check_openapi.py"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aegis.server import app

spec = (Path(__file__).resolve().parent.parent / "openapi.yaml").read_text()
want: dict[tuple[str, str], str] = {}
path = None
for line in spec.split("paths:", 1)[1].splitlines():
    if m := re.match(r"^  (/\S+):\s*$", line):
        path = m.group(1)
    elif m := re.match(r"^    (get|post|put|delete|patch):\s*$", line):
        method = m.group(1)
    elif m := re.match(r"^      operationId:\s*(\S+)", line):
        want[(method, path)] = m.group(1)

served = app.openapi()["paths"]
have = {(meth, p): op.get("operationId") for p, ops in served.items() for meth, op in ops.items()}
ok = True
for key, op_id in want.items():
    got = have.get(key)
    status = "ok " if got == op_id else "BAD"
    ok &= got == op_id
    print(f"{status} {key[0].upper():5} {key[1]:12} spec={op_id} served={got}")
sys.exit(0 if ok else 1)
