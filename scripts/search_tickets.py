#!/usr/bin/env python3
"""Administrative smoke test for the protected ticket-search operation."""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


parser = argparse.ArgumentParser(description="Run an authenticated read-only ticket search against the local service.")
parser.add_argument("assigned_to")
parser.add_argument("created_after")
parser.add_argument("created_before")
args = parser.parse_args()

project_dir = Path(__file__).resolve().parent.parent
token = load_env(project_dir / ".env").get("API_ACCESS_TOKEN", "")
if not token:
    raise SystemExit("API_ACCESS_TOKEN is not configured")

payload = json.dumps(
    {
        "assigned_to": args.assigned_to,
        "created_after": args.created_after,
        "created_before": args.created_before,
        "limit": 200,
    }
).encode("utf-8")
request = urllib.request.Request(
    "http://127.0.0.1:8085/tickets/search",
    data=payload,
    method="POST",
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    print(json.dumps(json.load(response), indent=2))
