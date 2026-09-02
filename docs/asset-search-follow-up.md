# Asset inventory search follow-up

## Finding

The Incident IQ integration user has been granted full read-only access to assets by the IIQ system manager. The current inability to answer inventory questions is therefore an integration-surface limitation, not an inherent consequence of read-only access.

The service currently exposes only:

- `iiq_get_asset` for lookup by IIQ asset record identifier; and
- `iiq_get_asset_by_tag` for an exact asset-tag lookup.

It does not expose an operation that lists, filters, aggregates, or counts assets. The disabled `iiq_advanced_read` escape hatch should not be enabled merely to work around this gap.

## Desired read-only capability

Add a named, bounded asset search operation capable of supporting questions such as:

- How many Chromebook Plus devices are available?
- How many assets were purchased during July 2026?
- Which matching devices are assigned, available, or in repair?

Candidate filters include:

- purchase-date range;
- asset type, category, manufacturer, and model;
- availability, assignment, and repair/status values;
- location; and
- exact or conservatively normalized asset identifiers.

## Implementation and safety requirements

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

Until this capability is implemented, the assistant should say that the current integration does not expose asset listing or filtering. It should not imply that read-only access inherently prevents inventory searches or counts.
