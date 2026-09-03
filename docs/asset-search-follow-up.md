# Asset inventory search follow-up

Status: implemented in version 0.2.0 as `iiq_search_assets`; version 0.3.0 added `iiq_export_assets_csv` for token-efficient bulk downloads.

## Finding

The Incident IQ integration user has been granted full read-only access to assets by the IIQ system manager. The current inability to answer inventory questions is therefore an integration-surface limitation, not an inherent consequence of read-only access.

The service previously exposed only:

- `iiq_get_asset` for lookup by IIQ asset record identifier; and
- `iiq_get_asset_by_tag` for an exact asset-tag lookup.

Version 0.2.0 closes that gap without enabling the disabled `iiq_advanced_read` escape hatch.

## Desired read-only capability

The named, bounded asset search operation supports questions such as:

- How many Chromebook Plus devices are available?
- How many assets were purchased during July 2026?
- Which matching devices are assigned, available, or in repair?

Supported filters include:

- purchase-date range;
- asset type, category, manufacturer, and model;
- availability, assignment, and repair values represented by IIQ asset statuses;
- location; and
- exact or conservatively normalized asset identifiers.

## Implementation and safety requirements

The live tenant confirmed the non-mutating `POST /api/v1.0/assets` query endpoint and its `Items` plus `Paging.TotalRows` response schema before implementation. The following safeguards are implemented:

1. Confirm the documented IIQ asset-list/search endpoint and request schema against the live tenant.
2. Test that endpoint with the existing integration credential. A successful response confirms the granted read-only asset access; an IIQ `401` or `403` would indicate a credential or role issue that must be resolved with the system manager.
3. Implement a named operation rather than exposing an arbitrary IIQ path or raw query language.
4. Allowlist filter fields and values, validate dates, and use conservative defaults.
5. Enforce pagination, a hard result limit, a bounded date window where appropriate, and a maximum number of upstream pages.
6. Return compact normalized asset summaries and an accurate total/count or truncation indicator. Do not infer a complete count from a truncated page.
7. Keep the operation strictly read-only and ensure secrets and complete raw asset records are absent from logs.
8. Add unit tests for validation, pagination, count accuracy, empty results, upstream permission failures, and response-size limits.
9. Run a live smoke test for the two reported scenarios before refreshing the OpenAPI tool definition in the technician assistant.

## Assistant wording

The assistant can now use `iiq_search_assets` for inventory listing and counts. It should continue to distinguish MCP surface limitations from IIQ credential or role failures.
