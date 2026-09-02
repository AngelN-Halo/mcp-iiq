#!/bin/sh
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if [ -e .env ]; then
    echo ".env already exists; leaving it unchanged"
    exit 0
fi

cp .env.example .env
chmod 600 .env
sed -i 's|^IIQ_BASE_URL=.*|IIQ_BASE_URL=|' .env
sed -i 's|^IIQ_API_TOKEN=.*|IIQ_API_TOKEN=|' .env
service_token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
sed -i "s|^API_ACCESS_TOKEN=.*|API_ACCESS_TOKEN=$service_token|" .env
unset service_token
echo "Created protected .env with a generated caller token; IIQ values remain blank"
