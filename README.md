# mcp-iiq

Read-only OpenAPI interface for Incident IQ. It is designed for registration as an OpenAPI tool in an AI/MCP environment while keeping the Incident IQ token server-side.

Despite the repository name, version 0.2 is intentionally an **OpenAPI service**, not a native stdio/SSE MCP transport. This makes it straightforward to register in Open WebUI or any client that imports OpenAPI. A native MCP adapter can be added later over the same guarded client.

## Safety model

- Downstream Incident IQ calls are HTTP `GET` plus a fixed allowlist of IIQ's non-mutating search/query `POST` endpoints.
- The service exposes no update, assignment, comment, close, delete, or bulk-mutation operation.
- The IIQ token is loaded only from the untracked `.env` file.
- Callers use a separate bearer token (`API_ACCESS_TOKEN`).
- Named tools cover common lookups; an advanced allowlisted GET tool is disabled by default.
- IIQ response bodies and secrets are not written to application logs.
- The container runs unprivileged, read-only, with Linux capabilities dropped.

## OpenAPI operations

- `iiq_get_ticket` — retrieve one ticket by IIQ identifier
- `iiq_get_ticket_by_number` — resolve and retrieve a ticket using its human-facing number, such as `T-100001`
- `iiq_get_ticket_timeline` — retrieve normalized comments and activity history by human-facing ticket number
- `iiq_get_technician_ticket_context` — retrieve the complete authorized ticket record and normalized timeline in one call
- `iiq_search_tickets` — search by exact assigned-technician name and inclusive creation-date window
- `iiq_search_tickets_by_requester` — search recent tickets by exact requested-for or submitted-by person
- `iiq_find_ticket_filters` — discover exact team/category names from IIQ's ticket taxonomy
- `iiq_search_tickets_filtered` — search a date window by an exact discovered team or category
- `iiq_get_asset` — retrieve one asset by IIQ record identifier
- `iiq_get_asset_by_tag` — exact asset-tag lookup
- `iiq_search_assets` — filter, list, and count assets by model, type, category, manufacturer, status, location, identifiers, or purchase-date window
- `iiq_get_user` — retrieve one user by IIQ identifier
- `iiq_list_locations` — retrieve visible locations
- `iiq_advanced_read` — optional, disabled-by-default allowlisted GET escape hatch

Keep `iiq_advanced_read` disabled unless an administrator has reviewed and approved a specific need that cannot be represented by a named operation. Named operations validate their input and expose a narrower, stable contract to AI clients.

The live schema is available at `http://<host>:8085/openapi.json`, and interactive documentation is at `/docs`.

## Configure on the server

```bash
cd ~/docker/mcp-iiq
./scripts/init_env.sh
nano .env
```

`init_env.sh` creates a mode-600 `.env`, leaves the IIQ tenant/token blank, and generates a separate random caller token. It will not overwrite an existing `.env`.

Set:

- `IIQ_BASE_URL` to the tenant root, such as `https://district.incidentiq.com`
- `IIQ_API_TOKEN` to the dedicated read-only token
- `IIQ_SITE_ID` and `IIQ_PRODUCT_ID` when required by Incident IQ
- `API_ACCESS_TOKEN` to a different long random value used by the OpenAPI caller

Never paste the IIQ token into an AI prompt, OpenAPI request, source file, or Git commit.

## Run and verify

```bash
docker compose build
./scripts/verify.sh
```

The verification script runs the unit tests, starts the service, validates the OpenAPI surface, checks authenticated fail-closed behavior, and confirms the service is reachable from NPM's `proxy` network.

The container joins the external network configured by `IIQ_INTERNAL_NETWORK` (default `iiq-internal`) and the existing Nginx Proxy Manager `proxy` network. Peers on either Docker network can use:

`http://mcp-iiq:8085/openapi.json`

## Nginx Proxy Manager

Create an NPM Proxy Host with these upstream settings:

- **Scheme:** `http`
- **Forward hostname/IP:** `mcp-iiq`
- **Forward port:** `8085`
- **Websockets:** optional/not required for this OpenAPI version
- **Cache assets:** off
- **Block common exploits:** on

Use the desired internal DNS name as the NPM domain. TLS can terminate upstream at Kemp as planned; NPM-to-container traffic remains HTTP on the private `proxy` Docker network. Do not publish this service without caller bearer authentication and appropriate upstream access controls.

Example caller test:

```bash
curl -sS \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  http://127.0.0.1:8085/tickets/12345
```

Administrative ticket-search smoke test (the script reads the caller token from `.env` without printing it):

```bash
./scripts/search_tickets.py "Alex Technician" 2026-08-20 2026-08-27
```

For natural-language category/team questions, call `iiq_find_ticket_filters` first. For example, `Google` may return several valid IIQ categories, while `SUPPORT TEAM` resolves to a team. Pass the selected result's exact `kind` and `name` to `iiq_search_tickets_filtered` with an inclusive date window. The two-step design avoids silently guessing among ambiguous taxonomy values.

For technician-assistant behavior, use the reusable prompt in `docs/technician-assistant-prompt.md`. It directs the assistant to call the consolidated context operation, distinguish ticket facts from recommendations, account for work already performed, identify missing information, and respect public versus internal timeline visibility.

Future write-enabled ticket correction and routing ideas are captured in `docs/future-ticket-adjustment-automation.md`. The initial design is recommendation-only with technician approval, exact taxonomy IDs, confidence/stop rules, and an audit trail.

Asset inventory questions use `iiq_search_assets`. The operation resolves human-readable IIQ model, type, category, manufacturer, status, and location filters, supports exact asset-tag/serial filters and bounded purchase-date windows, and returns both IIQ's filtered total and compact asset summaries. For example, use `model: "Chromebook Plus"` with `status: "Available"` to count available Chromebook Plus models, or a July 2026 `purchased_after`/`purchased_before` window to count purchases. Results are capped at 200 summaries over no more than five pages; `total_count` remains the exact IIQ count and `truncated` indicates whether summaries were omitted.

Example panel workflow:

1. Find ticket filters matching `Panel`.
2. Select the relevant category, such as `issuecategory` / `Interactive Panel/Displays: Connectivity`.
3. Search that exact category for the requested creation-date window. To cover every panel category, repeat the read-only search for each relevant result and combine the ticket summaries.

## Tests

```bash
python -m pytest -q
```

## Git and GitHub

The server directory is initialized as its own Git repository. After creating an empty GitHub repository:

```bash
cd ~/docker/mcp-iiq
git remote add origin git@github.com:YOUR-ORG/mcp-iiq.git
git push -u origin main
```

Review `git status` before every push. `.env` must remain untracked.
