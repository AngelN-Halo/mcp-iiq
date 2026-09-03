#!/bin/sh
set -eu

project_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$project_dir"

docker run --rm \
    -v "$project_dir/tests:/app/tests:ro" \
    mcp-iiq:0.2.0 \
    python -m pytest -q /app/tests

docker compose up -d

attempt=0
until curl -fsS http://127.0.0.1:8085/health >/tmp/mcp-iiq-health.json; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 15 ]; then
        echo "Service did not become healthy" >&2
        docker compose logs --tail=100
        exit 1
    fi
    sleep 1
done

cat /tmp/mcp-iiq-health.json
printf '\n'

python3 -c "import json,urllib.request; s=json.load(urllib.request.urlopen('http://127.0.0.1:8085/openapi.json')); methods={m for p in s['paths'].values() for m in p}; assert not {'put','patch','delete'} & methods; print('openapi_operations=' + ','.join(sorted(o['operationId'] for p in s['paths'].values() for o in p.values())))"

docker exec mcp-iiq python -c "import os,httpx; r=httpx.get('http://127.0.0.1:8085/locations',headers={'Authorization':'Bearer '+os.environ['API_ACCESS_TOKEN']}); print('authenticated_unconfigured_status='+str(r.status_code))"

docker run --rm \
    --network proxy \
    --entrypoint python \
    mcp-iiq:0.2.0 \
    -c "import urllib.request; print('proxy_network_health='+str(urllib.request.urlopen('http://mcp-iiq:8085/health',timeout=5).status))"

docker compose ps
