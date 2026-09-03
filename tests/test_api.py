from __future__ import annotations

import asyncio
import csv
import io
import json

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.client import IIQClient, validate_advanced_path
from app.config import Settings
from app.main import app, iiq, settings


client = TestClient(app)


def test_health_is_read_only() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "read-only"


def test_openapi_contains_no_mutation_operations() -> None:
    schema = client.get("/openapi.json").json()
    methods = {method for path in schema["paths"].values() for method in path}
    assert "delete" not in methods
    assert "put" not in methods
    assert "patch" not in methods
    operation_ids = {operation["operationId"] for path in schema["paths"].values() for operation in path.values()}
    assert all(not any(word in value for word in ("update", "delete", "assign", "close", "comment")) for value in operation_ids)


def test_requires_service_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))
    response = client.get("/locations")
    assert response.status_code == 401


def test_ticket_lookup_calls_expected_read_path(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_get(path, correlation_id, query=None):
        assert path == "tickets/12345"
        return {"TicketId": "12345"}

    monkeypatch.setattr(iiq, "get", fake_get)
    response = client.get("/tickets/12345", headers={"Authorization": "Bearer expected"})
    assert response.status_code == 200
    assert response.json()["data"]["TicketId"] == "12345"
    assert response.headers["X-Correlation-ID"]


def test_ticket_number_lookup_resolves_exact_match_then_gets_ticket(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_post_read_query(path, correlation_id, body, query=None):
        assert path == "search"
        assert body == {"Query": "T-100001"}
        return {
            "Item": {
                "Tickets": [
                    {"TicketId": "ticket-guid", "TicketNumber": "T-100001"},
                    {"TicketId": "other-guid", "TicketNumber": "T-1000010"},
                ]
            }
        }

    async def fake_get(path, correlation_id, query=None):
        assert path == "tickets/ticket-guid"
        return {"Item": {"TicketId": "ticket-guid", "TicketNumber": "T-100001"}}

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    monkeypatch.setattr(iiq, "get", fake_get)
    response = client.get(
        "/tickets/by-number/100001",
        headers={"Authorization": "Bearer expected"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["Item"]["TicketNumber"] == "T-100001"


def test_ticket_number_lookup_rejects_non_ticket_search_text(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))
    response = client.get(
        "/tickets/by-number/recent-tickets",
        headers={"Authorization": "Bearer expected"},
    )
    assert response.status_code == 400


def test_ticket_timeline_returns_normalized_comments_and_events(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_post_read_query(path, correlation_id, body, query=None):
        return {"Item": {"Tickets": [{"TicketId": "ticket-guid", "TicketNumber": "T-100002"}]}}

    async def fake_get(path, correlation_id, query=None):
        assert path == "tickets/ticket-guid/activities"
        assert query == {"$s": 25, "$p": 0, "$o": "CreatedDate ASC"}
        return {
            "Items": [
                {
                    "CreatedDate": "2026-08-26T12:47:43.567",
                    "Owner": {"Name": "Taylor Technician"},
                    "IsPublic": True,
                    "ActivityItems": [
                        {
                            "TicketActivityTypeId": 6,
                            "CreatedDate": "2026-08-26T12:47:43.597",
                            "Comments": "<p>Will do.</p>",
                        }
                    ],
                },
                {
                    "CreatedDate": "2026-08-26T10:45:08.377",
                    "Owner": {"Name": "Alex Technician"},
                    "IsPublic": True,
                    "ActivityItems": [
                        {"TicketActivityTypeId": 63, "Notes": 'Status changed from "Submitted" to "In Progress"'}
                    ],
                },
            ]
        }

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    monkeypatch.setattr(iiq, "get", fake_get)
    response = client.get(
        "/tickets/by-number/T-100002/timeline?limit=25",
        headers={"Authorization": "Bearer expected"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert response.json()["entries"][0]["activity_type"] == "status_change"
    assert response.json()["entries"][1] == {
        "timestamp": "2026-08-26T12:47:43.597",
        "actor": "Taylor Technician",
        "activity_type": "comment",
        "is_public": True,
        "text": "Will do.",
    }


def test_technician_context_combines_full_ticket_item_and_timeline(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_post_read_query(path, correlation_id, body, query=None):
        return {"Item": {"Tickets": [{"TicketId": "ticket-guid", "TicketNumber": "T-100002"}]}}

    async def fake_get(path, correlation_id, query=None):
        if path == "tickets/ticket-guid":
            return {
                "Item": {
                    "TicketId": "ticket-guid",
                    "TicketNumber": "T-100002",
                    "IssueDescription": "Printing is delayed",
                    "CustomFieldValues": [{"Name": "Impact", "Value": "District-wide"}],
                    "Assets": [{"AssetTag": "MFD-1"}],
                },
                "UserToken": "must-not-be-returned",
            }
        assert path == "tickets/ticket-guid/activities"
        assert query == {"$s": 200, "$p": 0, "$o": "CreatedDate ASC"}
        return {
            "Items": [
                {
                    "Owner": {"Name": "Alex Technician"},
                    "IsPublic": False,
                    "ActivityItems": [
                        {"TicketActivityTypeId": 6, "CreatedDate": "2026-08-26T12:38:37", "Comments": "Investigated."}
                    ],
                }
            ]
        }

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    monkeypatch.setattr(iiq, "get", fake_get)
    response = client.get(
        "/tickets/by-number/T-100002/technician-context",
        headers={"Authorization": "Bearer expected"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["ticket"]["IssueDescription"] == "Printing is delayed"
    assert result["ticket"]["Assets"] == [{"AssetTag": "MFD-1"}]
    assert "UserToken" not in result["ticket"]
    assert result["timeline_count"] == 1
    assert result["timeline"][0]["is_public"] is False


def test_ticket_search_uses_read_query_and_filters_exact_assignee(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_post_read_query(path, correlation_id, body, query=None):
        if path == "search/v2":
            return {"Items": [{"Id": "agent-guid", "Name": "Alex Technician"}]}
        assert path == "tickets"
        assert body["Filters"][0]["Facet"] == "createddate"
        assert body["Filters"][0]["Value"] == "daterange:08/20/2026-08/27/2026"
        assert body["Filters"][1] == {"Facet": "agent", "Id": "agent-guid"}
        return {
            "Items": [
                {
                    "TicketId": "one",
                    "TicketNumber": "100",
                    "WorkflowStep": {"StatusName": "In Progress"},
                    "AssignedToTeam": {"TeamName": "SUPPORT TEAM"},
                    "AssignedToUser": {"Name": "Alex Technician"},
                    "Owner": {"Name": "Requester Name"},
                },
            ]
        }

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    response = client.post(
        "/tickets/search",
        headers={"Authorization": "Bearer expected"},
        json={"assigned_to": "Alex Technician", "created_after": "2026-08-20", "created_before": "2026-08-27"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["tickets"][0]["ticket_number"] == "100"
    assert response.json()["tickets"][0]["status"] == "In Progress"
    assert response.json()["tickets"][0]["assigned_team"] == "SUPPORT TEAM"
    assert response.json()["tickets"][0]["assigned_to"] == "Alex Technician"


def test_ticket_summary_never_treats_requestor_as_assignee(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_post_read_query(path, correlation_id, body, query=None):
        if path == "filters":
            return {"Items": [{"Facet": "team", "Name": "SUPPORT TEAM", "Id": "team-guid"}]}
        return {"Items": [{"TicketNumber": "100003", "Owner": {"Name": "Jamie Requester"}}]}

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    response = client.post(
        "/tickets/search-filtered",
        headers={"Authorization": "Bearer expected"},
        json={"filter_kind": "team", "filter_name": "SUPPORT TEAM", "created_after": "2026-08-17"},
    )
    assert response.status_code == 200
    assert response.json()["tickets"][0]["assigned_to"] is None


def test_find_ticket_filters_returns_only_compact_allowed_metadata(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_post_read_query(path, correlation_id, body, query=None):
        assert path == "filters"
        assert body["Facets"] == ["team", "issuecategory"]
        return {
            "Items": [
                {"Facet": "team", "Name": "SUPPORT TEAM", "Id": "team-guid", "Secret": "not returned"},
                {"Facet": "unapproved", "Name": "Hidden", "Id": "hidden-guid"},
            ]
        }

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    response = client.post(
        "/ticket-filters/find",
        headers={"Authorization": "Bearer expected"},
        json={"query": "engineering", "kinds": ["team", "issuecategory"]},
    )
    assert response.status_code == 200
    assert response.json()["matches"] == [{"kind": "team", "name": "SUPPORT TEAM"}]


def test_filtered_ticket_search_resolves_exact_name_and_queries_ticket_facet(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_post_read_query(path, correlation_id, body, query=None):
        if path == "filters":
            return {"Items": [{"Facet": "team", "Name": "SUPPORT TEAM", "Id": "team-guid"}]}
        assert path == "tickets"
        assert body["Filters"][0]["Value"] == "daterange:08/20/2026-08/27/2026"
        assert body["Filters"][1] == {"Facet": "team", "Id": "team-guid"}
        return {"Items": [{"TicketNumber": "100004", "Issue": {"Name": "Printer Issues"}}]}

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    response = client.post(
        "/tickets/search-filtered",
        headers={"Authorization": "Bearer expected"},
        json={
            "filter_kind": "team",
            "filter_name": "SUPPORT TEAM",
            "created_after": "2026-08-20",
            "created_before": "2026-08-27",
        },
    )
    assert response.status_code == 200
    assert response.json()["filter_name"] == "SUPPORT TEAM"
    assert response.json()["tickets"][0]["category"] == "Printer Issues"


def test_requester_search_checks_requested_for_and_submitted_by_then_deduplicates(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))
    ticket_calls = []

    async def fake_post_read_query(path, correlation_id, body, query=None):
        if path == "search/v2":
            return {"Items": [{"Id": "user-guid", "Name": "Jordan Requester"}]}
        assert path == "tickets"
        ticket_calls.append(body["Filters"][1])
        return {
            "Items": [
                {
                    "TicketId": "ticket-guid",
                    "TicketNumber": "T-100001",
                    "TicketCreatedDate": "2026-08-26T10:35:04.69",
                    "For": {"Name": "Jordan Requester"},
                    "Owner": {"Name": "Jordan Requester"},
                }
            ]
        }

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    response = client.post(
        "/tickets/search-by-requester",
        headers={"Authorization": "Bearer expected"},
        json={
            "requester": "Jordan Requester",
            "created_after": "2026-08-20",
            "created_before": "2026-08-28",
        },
    )
    assert response.status_code == 200
    assert ticket_calls == [
        {"Facet": "user", "Id": "user-guid"},
        {"Facet": "submittedby", "Id": "user-guid"},
    ]
    assert response.json()["count"] == 1
    assert response.json()["tickets"][0]["ticket_number"] == "T-100001"
    assert response.json()["tickets"][0]["requested_for"] == "Jordan Requester"


def test_advanced_path_is_allowlisted() -> None:
    configured = Settings(iiq_allowed_read_prefixes="tickets,assets")
    assert validate_advanced_path("tickets/12345", configured) == "tickets/12345"


def test_advanced_path_rejects_non_allowlisted_resource() -> None:
    configured = Settings(iiq_allowed_read_prefixes="tickets,assets")
    try:
        validate_advanced_path("admin/settings", configured)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("non-allowlisted resource was accepted")


def test_asset_search_resolves_filters_paginates_and_returns_compact_results(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))
    asset_calls = []

    async def fake_post_read_query(path, correlation_id, body, query=None):
        if path == "filters":
            facet = body["Facets"][0]
            assert body["ResultsFilter"]["EntityName"] == "assets"
            if facet == "model":
                return {
                    "Items": [
                        {"Facet": "model", "Name": "Example Chromebook Plus 14", "Id": "model-1"},
                        {"Facet": "model", "Name": "Example Chromebook Plus 15", "Id": "model-2"},
                    ]
                }
            assert facet == "assetstatus"
            return {
                "Items": [
                    {"Facet": "assetstatus", "Name": "Available", "Id": "available-id"},
                    {"Facet": "assetstatus", "Name": "Available for Parts", "Id": "parts-id"},
                ]
            }
        assert path == "assets"
        asset_calls.append((body, query))
        assert body["Schema"] == "All"
        assert body["Filters"] == [
            {"Facet": "model", "Id": "model-1", "GroupIndex": 0},
            {"Facet": "model", "Id": "model-2", "GroupIndex": 0},
            {"Facet": "assetstatus", "Id": "available-id", "GroupIndex": 0},
        ]
        page = query["$p"]
        items = [
            {
                "AssetId": f"asset-{page * 2 + index}",
                "AssetTag": f"TEST-{page * 2 + index}",
                "SerialNumber": "synthetic",
                "Model": {
                    "Name": "Example Chromebook Plus 14",
                    "Manufacturer": {"Name": "Example Manufacturer"},
                    "Category": {"Name": "Laptop"},
                },
                "Status": {"Name": "Available"},
                "Location": {"Name": "Example School"},
                "Secret": "must-not-leak",
            }
            for index in range(2 if page == 0 else 1)
        ]
        return {"Items": items, "Paging": {"TotalRows": 3}}

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    response = client.post(
        "/assets/search",
        headers={"Authorization": "Bearer expected"},
        json={"model": "Chromebook Plus", "status": "Available", "page_size": 2, "limit": 10},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["total_count"] == 3
    assert result["returned_count"] == 3
    assert result["pages_scanned"] == 2
    assert result["truncated"] is False
    assert result["assets"][0]["manufacturer"] == "Example Manufacturer"
    assert "Secret" not in result["assets"][0]
    assert [call[1]["$p"] for call in asset_calls] == [0, 1]


def test_asset_search_purchase_dates_and_result_limit_are_bounded(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_post_read_query(path, correlation_id, body, query=None):
        assert path == "assets"
        assert body["Filters"] == [
            {"Facet": "purchaseddate", "Value": "daterange:07/01/2026-07/31/2026", "GroupIndex": 0}
        ]
        assert query["$s"] == 2
        return {
            "Items": [{"AssetId": "one"}, {"AssetId": "two"}],
            "Paging": {"TotalRows": 50},
        }

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    response = client.post(
        "/assets/search",
        headers={"Authorization": "Bearer expected"},
        json={"purchased_after": "2026-07-01", "purchased_before": "2026-07-31", "limit": 2},
    )
    assert response.status_code == 200
    assert response.json()["total_count"] == 50
    assert response.json()["truncated"] is True


def test_asset_search_rejects_partial_or_excessive_date_windows(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))
    partial = client.post(
        "/assets/search",
        headers={"Authorization": "Bearer expected"},
        json={"purchased_after": "2026-07-01"},
    )
    excessive = client.post(
        "/assets/search",
        headers={"Authorization": "Bearer expected"},
        json={"purchased_after": "2025-01-01", "purchased_before": "2026-07-31"},
    )
    assert partial.status_code == 422
    assert excessive.status_code == 422


def test_asset_search_handles_empty_results(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_post_read_query(path, correlation_id, body, query=None):
        assert path == "assets"
        return {"Items": [], "Paging": {"TotalRows": 0}}

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    response = client.post("/assets/search", headers={"Authorization": "Bearer expected"}, json={})
    assert response.status_code == 200
    assert response.json()["assets"] == []
    assert response.json()["total_count"] == 0


def test_asset_search_propagates_iiq_permission_failure(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))

    async def fake_post_read_query(path, correlation_id, body, query=None):
        raise HTTPException(502, "Incident IQ rejected the integration credential or its permissions")

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    response = client.post("/assets/search", headers={"Authorization": "Bearer expected"}, json={})
    assert response.status_code == 502
    assert "permissions" in response.json()["detail"]


def test_asset_query_enforces_upstream_response_size_limit(monkeypatch) -> None:
    configured = Settings(iiq_base_url="https://example.invalid", iiq_api_token="synthetic", iiq_max_response_bytes=10)
    test_client = IIQClient(configured)

    class FakeResponse:
        content = b"12345678901"
        status_code = 200

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.client.httpx.AsyncClient", FakeAsyncClient)
    try:
        asyncio.run(test_client.post_read_query("assets", "test-correlation", {"Filters": []}))
    except HTTPException as exc:
        assert exc.status_code == 502
        assert "size limit" in exc.detail
    else:
        raise AssertionError("oversized Incident IQ response was accepted")


def test_asset_csv_export_creates_bounded_download_without_rows_in_tool_response(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))
    monkeypatch.setattr(settings, "iiq_export_max_rows", 25_000)
    monkeypatch.setattr(settings, "iiq_export_page_size", 1)
    monkeypatch.setattr(settings, "iiq_report_ttl_seconds", 900)
    monkeypatch.setattr(settings, "iiq_public_base_url", "")
    monkeypatch.setattr("app.main.REPORT_DIRECTORY", tmp_path)
    calls = []

    async def fake_post_read_query(path, correlation_id, body, query=None):
        assert path == "assets"
        calls.append(query)
        index = query["$p"]
        return {
            "Items": [
                {
                    "AssetId": f"asset-{index}",
                    "AssetTag": "=FORMULA" if index == 0 else f"TEST-{index}",
                    "SerialNumber": f"SERIAL-{index}",
                    "Model": {"Name": "Synthetic Model"},
                    "Status": {"Name": "Available"},
                }
            ],
            "Paging": {"TotalRows": 3},
        }

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    response = client.post(
        "/assets/export",
        headers={"Authorization": "Bearer expected"},
        json={"max_rows": 2},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["total_count"] == 3
    assert result["exported_count"] == 2
    assert result["truncated"] is True
    assert "assets" not in result
    assert [call["$p"] for call in calls] == [0, 1]

    download = client.get(result["download_url"])
    assert download.status_code == 200
    assert download.headers["cache-control"] == "no-store, private"
    rows = list(csv.DictReader(io.StringIO(download.content.decode("utf-8-sig"))))
    assert len(rows) == 2
    assert rows[0]["AssetTag"] == "'=FORMULA"
    assert list(rows[0]) == [
        "AssetId", "AssetTag", "SerialNumber", "Name", "AssetType", "Category",
        "Manufacturer", "Model", "Status", "Location", "Room", "PurchasedDate",
    ]


def test_asset_csv_download_expires_and_removes_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "api_access_token", type(settings.api_access_token)("expected"))
    monkeypatch.setattr("app.main.REPORT_DIRECTORY", tmp_path)

    async def fake_post_read_query(path, correlation_id, body, query=None):
        return {"Items": [], "Paging": {"TotalRows": 0}}

    monkeypatch.setattr(iiq, "post_read_query", fake_post_read_query)
    created = client.post(
        "/assets/export",
        headers={"Authorization": "Bearer expected"},
        json={"max_rows": 1},
    ).json()
    metadata_path = next(tmp_path.glob("*.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["expires_at"] = 0
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    expired = client.get(created["download_url"])
    assert expired.status_code == 404
    assert not list(tmp_path.iterdir())
    assert client.get("/reports/assets/not-a-valid-token").status_code == 404
