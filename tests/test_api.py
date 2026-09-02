from __future__ import annotations

from fastapi.testclient import TestClient

from app.client import validate_advanced_path
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
