from __future__ import annotations

import hmac
import html
import logging
import re
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .client import IIQClient, safe_segment, validate_advanced_path
from .config import get_settings
from .models import (
    AdvancedReadRequest,
    FilteredTicketSearchRequest,
    FilteredTicketSearchResponse,
    HealthResponse,
    IIQResult,
    RequesterTicketSearchRequest,
    RequesterTicketSearchResponse,
    TicketFilterCandidate,
    TicketFilterLookupRequest,
    TicketFilterLookupResponse,
    TicketSearchRequest,
    TicketSearchResponse,
    TicketSummary,
    TechnicianTicketContextResponse,
    TicketTimelineEntry,
    TicketTimelineResponse,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = get_settings()
iiq = IIQClient(settings)

app = FastAPI(
    title="Incident IQ Read-Only API",
    version="0.1.0",
    description=(
        "Read-only, least-privileged OpenAPI interface for Incident IQ. "
        "This service implements only downstream HTTP GET requests and contains no ticket mutation operations."
    ),
)
bearer = HTTPBearer(
    auto_error=False,
    description="Service bearer token configured by the administrator. The Incident IQ token is never supplied by the model.",
)


async def require_caller(credentials: HTTPAuthorizationCredentials | None = Security(bearer)) -> None:
    expected = settings.api_access_token.get_secret_value()
    if not expected:
        raise HTTPException(503, "Caller authentication is not configured")
    supplied = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Invalid service credential")


def correlation_id(request: Request, response: Response) -> str:
    incoming = request.headers.get("X-Correlation-ID", "").strip()
    value = incoming[:128] if incoming else str(uuid.uuid4())
    response.headers["X-Correlation-ID"] = value
    return value


@app.get("/health", response_model=HealthResponse, include_in_schema=False)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        iiq_configured=settings.iiq_is_configured,
        mode="read-only",
        advanced_read_enabled=settings.iiq_enable_advanced_read,
    )


@app.get(
    "/tickets/{ticket_id}",
    operation_id="iiq_get_ticket",
    summary="Get one Incident IQ ticket",
    description="Retrieve one authorized ticket by Incident IQ ticket identifier. This operation cannot modify the ticket.",
    response_model=IIQResult,
    dependencies=[Depends(require_caller)],
    tags=["tickets"],
)
async def get_ticket(ticket_id: str, request: Request, response: Response) -> IIQResult:
    cid = correlation_id(request, response)
    path = f"tickets/{safe_segment(ticket_id, 'ticket ID')}"
    return IIQResult(correlation_id=cid, resource="ticket", data=await iiq.get(path, cid))


def _normalize_ticket_number(value: str) -> str:
    candidate = value.strip().upper()
    if candidate.isdigit():
        candidate = f"T-{candidate}"
    if not re.fullmatch(r"T-\d{1,12}", candidate):
        raise HTTPException(400, "Ticket number must use the format T-123456")
    return candidate


def _exact_ticket_ids(value, ticket_number: str) -> set[str]:
    matches: set[str] = set()
    if isinstance(value, dict):
        number = _display(_ci_get(value, "TicketNumber"))
        identifier = _display(_ci_get(value, "TicketId", "TicketID", "Id"))
        if number and identifier and number.strip().upper() == ticket_number:
            matches.add(identifier)
        for nested in value.values():
            matches.update(_exact_ticket_ids(nested, ticket_number))
    elif isinstance(value, list):
        for nested in value:
            matches.update(_exact_ticket_ids(nested, ticket_number))
    return matches


async def _resolve_ticket_id(ticket_number: str, cid: str) -> tuple[str, str]:
    normalized = _normalize_ticket_number(ticket_number)
    raw = await iiq.post_read_query("search", cid, {"Query": normalized})
    ticket_ids = _exact_ticket_ids(raw, normalized)
    if not ticket_ids:
        raise HTTPException(404, "No exact Incident IQ ticket-number match was found")
    if len(ticket_ids) > 1:
        raise HTTPException(409, "More than one exact Incident IQ ticket-number match was found")
    return normalized, next(iter(ticket_ids))


@app.get(
    "/tickets/by-number/{ticket_number}",
    operation_id="iiq_get_ticket_by_number",
    summary="Get one Incident IQ ticket by ticket number",
    description=(
        "Resolve a human-facing ticket number such as T-100001 to its IIQ identifier, then retrieve the authorized ticket. "
        "This operation uses IIQ's non-mutating search and GET endpoints and cannot change the ticket."
    ),
    response_model=IIQResult,
    dependencies=[Depends(require_caller)],
    tags=["tickets"],
)
async def get_ticket_by_number(ticket_number: str, request: Request, response: Response) -> IIQResult:
    cid = correlation_id(request, response)
    _, ticket_id = await _resolve_ticket_id(ticket_number, cid)
    data = await iiq.get(f"tickets/{safe_segment(ticket_id, 'ticket ID')}", cid)
    return IIQResult(correlation_id=cid, resource="ticket", data=data)


_ACTIVITY_TYPES = {
    6: "comment",
    8: "resolution_action",
    63: "status_change",
    70: "follower_change",
}


def _clean_activity_text(value) -> str | None:
    text = _display(value)
    if not text:
        return None
    without_tags = re.sub(r"<[^>]+>", " ", html.unescape(text))
    cleaned = " ".join(without_tags.split())
    return cleaned[:12000] or None


def _timeline_entries(raw) -> list[TicketTimelineEntry]:
    activities = _ci_get(raw, "Items") if isinstance(raw, dict) else raw
    if not isinstance(activities, list):
        raise HTTPException(502, "Incident IQ timeline returned an unexpected response shape")
    entries: list[TicketTimelineEntry] = []
    for activity in activities:
        if not isinstance(activity, dict):
            continue
        actor = _display(_ci_get(activity, "Owner", "ByUser"))
        outer_timestamp = _display(_ci_get(activity, "CreatedDate", "ModifiedDate"))
        is_public = _ci_get(activity, "IsPublic")
        items = _ci_get(activity, "ActivityItems")
        if not isinstance(items, list) or not items:
            items = [activity]
        for item in items:
            if not isinstance(item, dict):
                continue
            type_id = _ci_get(item, "TicketActivityTypeId")
            try:
                numeric_type = int(type_id) if type_id is not None else None
            except (TypeError, ValueError):
                numeric_type = None
            activity_type = _ACTIVITY_TYPES.get(numeric_type, f"activity_{numeric_type}" if numeric_type else "activity")
            text = _clean_activity_text(_ci_get(item, "Comments", "Notes", "Description", "Message"))
            timestamp = _display(_ci_get(item, "CreatedDate", "ModifiedDate")) or outer_timestamp
            entries.append(
                TicketTimelineEntry(
                    timestamp=timestamp,
                    actor=actor,
                    activity_type=activity_type,
                    is_public=is_public if isinstance(is_public, bool) else None,
                    text=text,
                )
            )
    return sorted(entries, key=lambda entry: entry.timestamp or "")


@app.get(
    "/tickets/by-number/{ticket_number}/timeline",
    operation_id="iiq_get_ticket_timeline",
    summary="Get a ticket's comments and activity timeline",
    description=(
        "Resolve a human-facing ticket number, then return its authorized comments, status changes, actions, and other "
        "timeline events in chronological order. This operation cannot add or modify activities."
    ),
    response_model=TicketTimelineResponse,
    dependencies=[Depends(require_caller)],
    tags=["tickets"],
)
async def get_ticket_timeline(
    ticket_number: str,
    request: Request,
    response: Response,
    limit: int = Query(default=100, ge=1, le=200),
) -> TicketTimelineResponse:
    cid = correlation_id(request, response)
    normalized, ticket_id = await _resolve_ticket_id(ticket_number, cid)
    raw = await iiq.get(
        f"tickets/{safe_segment(ticket_id, 'ticket ID')}/activities",
        cid,
        {"$s": limit, "$p": 0, "$o": "CreatedDate ASC"},
    )
    entries = _timeline_entries(raw)[:limit]
    return TicketTimelineResponse(
        correlation_id=cid,
        ticket_number=normalized,
        count=len(entries),
        entries=entries,
    )


def _ticket_item(raw) -> dict:
    item = _ci_get(raw, "Item") if isinstance(raw, dict) else None
    if not isinstance(item, dict):
        raise HTTPException(502, "Incident IQ ticket lookup returned an unexpected response shape")
    return item


@app.get(
    "/tickets/by-number/{ticket_number}/technician-context",
    operation_id="iiq_get_technician_ticket_context",
    summary="Get complete read-only ticket context for technician review",
    description=(
        "Resolve a human-facing ticket number and return the complete authorized IIQ ticket record together with its "
        "normalized work, comment, status, and activity timeline. Use this operation when reviewing completed work, "
        "identifying missing information, or suggesting next steps. It cannot modify the ticket."
    ),
    response_model=TechnicianTicketContextResponse,
    dependencies=[Depends(require_caller)],
    tags=["tickets"],
)
async def get_technician_ticket_context(
    ticket_number: str,
    request: Request,
    response: Response,
    timeline_limit: int = Query(default=200, ge=1, le=500),
) -> TechnicianTicketContextResponse:
    cid = correlation_id(request, response)
    normalized, ticket_id = await _resolve_ticket_id(ticket_number, cid)
    safe_id = safe_segment(ticket_id, "ticket ID")
    ticket_raw = await iiq.get(f"tickets/{safe_id}", cid)
    timeline_raw = await iiq.get(
        f"tickets/{safe_id}/activities",
        cid,
        {"$s": timeline_limit, "$p": 0, "$o": "CreatedDate ASC"},
    )
    timeline = _timeline_entries(timeline_raw)[:timeline_limit]
    return TechnicianTicketContextResponse(
        correlation_id=cid,
        ticket_number=normalized,
        ticket=_ticket_item(ticket_raw),
        timeline_count=len(timeline),
        timeline=timeline,
    )


def _ci_get(record: dict, *names: str):
    index = {str(key).casefold(): value for key, value in record.items()}
    for name in names:
        if name.casefold() in index:
            return index[name.casefold()]
    return None


def _display(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        nested = _ci_get(
            value,
            "Name",
            "FullName",
            "DisplayName",
            "StatusName",
            "StepName",
            "TeamName",
            "IssueName",
            "IssueCategoryName",
            "Value",
            "Label",
        )
        return str(nested) if nested is not None else None
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def _ticket_summary(record: dict) -> TicketSummary:
    # IIQ's Owner is the requestor, not the assigned technician. Leave this null
    # when assignment data is absent instead of misidentifying the requestor.
    assignee = _display(_ci_get(record, "AssignedToUser", "AssignedTo", "Agent", "AssignedAgent"))
    assignee = assignee or _display(_ci_get(record, "AssignedToUserName", "AgentName", "AssignedToName"))
    return TicketSummary(
        ticket_id=_display(_ci_get(record, "TicketId", "TicketID", "Id")),
        ticket_number=_display(_ci_get(record, "TicketNumber", "Number")),
        subject=_display(_ci_get(record, "Subject", "ItemDescription", "IssueDescription", "Title")),
        created_at=_display(_ci_get(record, "TicketCreatedDate", "CreatedDate", "SubmittedDate", "CreatedAt")),
        status=_display(_ci_get(record, "WorkflowStep", "Status", "StatusName", "TicketStatus")),
        assigned_to=assignee,
        assigned_team=_display(_ci_get(record, "AssignedToTeam", "Team", "AssignedTeam")),
        location=_display(_ci_get(record, "Location", "LocationName", "ForLocation")),
        priority=_display(_ci_get(record, "Priority", "PriorityName", "TicketPriority")),
        category=_display(_ci_get(record, "Issue", "IssueName", "IssueCategory", "IssueCategoryName")),
        requested_for=_display(_ci_get(record, "For", "RequestedFor", "ForUser")),
        submitted_by=_display(_ci_get(record, "Owner", "SubmittedBy", "SubmittedByUser")),
    )


def _exact_named_ids(value, wanted: str) -> set[str]:
    matches: set[str] = set()
    if isinstance(value, dict):
        name = _display(_ci_get(value, "Name", "FullName", "DisplayName"))
        identifier = _display(_ci_get(value, "UserId", "EntityId", "Id"))
        if name and identifier and " ".join(name.split()).casefold() == wanted:
            matches.add(identifier)
        for nested in value.values():
            matches.update(_exact_named_ids(nested, wanted))
    elif isinstance(value, list):
        for nested in value:
            matches.update(_exact_named_ids(nested, wanted))
    return matches


_TICKET_FILTER_KINDS = {"team", "issuecategory", "issuetype", "issue"}


def _filter_candidates(value) -> list[tuple[str, str, str]]:
    """Extract only approved ticket-filter metadata from IIQ's nested response."""
    matches: set[tuple[str, str, str]] = set()
    if isinstance(value, dict):
        kind = _display(_ci_get(value, "Facet"))
        name = _display(_ci_get(value, "Name"))
        identifier = _display(_ci_get(value, "Id"))
        if kind and name and identifier and kind.casefold() in _TICKET_FILTER_KINDS:
            matches.add((kind.casefold(), name, identifier))
        for nested in value.values():
            matches.update(_filter_candidates(nested))
    elif isinstance(value, list):
        for nested in value:
            matches.update(_filter_candidates(nested))
    return sorted(matches, key=lambda item: (item[0], item[1].casefold(), item[2]))


async def _find_filters(query: str, kinds: list[str], cid: str) -> list[tuple[str, str, str]]:
    raw = await iiq.post_read_query(
        "filters",
        cid,
        {
            "Facets": kinds,
            "Query": query,
            "ResultsFilter": {"EntityName": "tickets", "ShowAll": False, "ShowDeleted": False},
        },
    )
    allowed = set(kinds)
    return [candidate for candidate in _filter_candidates(raw) if candidate[0] in allowed]


def _ticket_query_payload(start, end, filter_kind: str, filter_id: str) -> dict:
    date_value = f"daterange:{start:%m/%d/%Y}-{end:%m/%d/%Y}"
    return {
        "ProductId": settings.iiq_product_id or None,
        "Schema": "All",
        "Filters": [
            {
                "Facet": "createddate",
                "FacetName": None,
                "Id": None,
                "Name": date_value,
                "Negative": False,
                "Selected": True,
                "Value": date_value,
                "GroupIndex": 0,
            },
            {"Facet": filter_kind, "Id": filter_id},
        ],
    }


def _ticket_records(raw) -> list[dict]:
    records = _ci_get(raw, "Items") if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise HTTPException(502, "Incident IQ ticket search returned an unexpected response shape")
    return [record for record in records if isinstance(record, dict)]


async def _resolve_exact_user_id(display_name: str, cid: str) -> str:
    wanted = " ".join(display_name.split()).casefold()
    user_search = await iiq.post_read_query(
        "search/v2",
        cid,
        {"Query": f'"{display_name}"', "Facets": 4, "IncludeMatchedItem": False},
    )
    user_ids = _exact_named_ids(user_search, wanted)
    if not user_ids:
        raise HTTPException(404, "No exact Incident IQ user match was found")
    if len(user_ids) > 1:
        raise HTTPException(409, "More than one exact Incident IQ user match was found; use a unique identity")
    return next(iter(user_ids))


@app.post(
    "/tickets/search-by-requester",
    operation_id="iiq_search_tickets_by_requester",
    summary="Search recent tickets by requester name",
    description=(
        "Find tickets associated with an exact requester display name. Searches both IIQ requested-for and submitted-by "
        "relationships, merges duplicate tickets, and defaults to the most recent 30 days. This cannot modify tickets."
    ),
    response_model=RequesterTicketSearchResponse,
    dependencies=[Depends(require_caller)],
    tags=["tickets"],
)
async def search_tickets_by_requester(
    body: RequesterTicketSearchRequest, request: Request, response: Response
) -> RequesterTicketSearchResponse:
    try:
        start, end = body.date_bounds()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    cid = correlation_id(request, response)
    user_id = await _resolve_exact_user_id(body.requester, cid)
    records: list[dict] = []
    for facet in ("user", "submittedby"):
        raw = await iiq.post_read_query(
            "tickets",
            cid,
            _ticket_query_payload(start, end, facet, user_id),
            {"$s": body.limit, "$p": 0, "$o": "TicketCreatedDate DESC"},
        )
        records.extend(_ticket_records(raw))
    unique: dict[str, TicketSummary] = {}
    for record in records:
        summary = _ticket_summary(record)
        key = summary.ticket_id or summary.ticket_number
        if key and key not in unique:
            unique[key] = summary
    tickets = sorted(unique.values(), key=lambda ticket: ticket.created_at or "", reverse=True)[: body.limit]
    return RequesterTicketSearchResponse(
        correlation_id=cid,
        requester=body.requester,
        created_after=start,
        created_before=end,
        count=len(tickets),
        tickets=tickets,
    )


@app.post(
    "/ticket-filters/find",
    operation_id="iiq_find_ticket_filters",
    summary="Find valid IIQ ticket teams and categories",
    description=(
        "Find IIQ's exact assignment-team and ticket-taxonomy names before searching tickets. "
        "For an ambiguous phrase such as Google Account issues, select the intended result and pass its exact kind and name to iiq_search_tickets_filtered."
    ),
    response_model=TicketFilterLookupResponse,
    dependencies=[Depends(require_caller)],
    tags=["tickets"],
)
async def find_ticket_filters(
    body: TicketFilterLookupRequest, request: Request, response: Response
) -> TicketFilterLookupResponse:
    cid = correlation_id(request, response)
    candidates = await _find_filters(body.query, list(dict.fromkeys(body.kinds)), cid)
    public_matches = [TicketFilterCandidate(kind=kind, name=name) for kind, name, _ in candidates[: body.limit]]
    return TicketFilterLookupResponse(
        correlation_id=cid,
        query=body.query,
        count=len(public_matches),
        matches=public_matches,
    )


@app.post(
    "/tickets/search-filtered",
    operation_id="iiq_search_tickets_filtered",
    summary="Search recent tickets by exact team or category",
    description=(
        "Search an inclusive creation-date window using an exact team or taxonomy value returned by iiq_find_ticket_filters. "
        "This performs only IIQ's non-mutating read/query operations and cannot change tickets."
    ),
    response_model=FilteredTicketSearchResponse,
    dependencies=[Depends(require_caller)],
    tags=["tickets"],
)
async def search_tickets_filtered(
    body: FilteredTicketSearchRequest, request: Request, response: Response
) -> FilteredTicketSearchResponse:
    try:
        start, end = body.date_bounds()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    cid = correlation_id(request, response)
    candidates = await _find_filters(body.filter_name, [body.filter_kind], cid)
    wanted = body.filter_name.casefold()
    exact = {(kind, name, identifier) for kind, name, identifier in candidates if name.casefold() == wanted}
    if not exact:
        raise HTTPException(404, "No exact IIQ filter match was found; call iiq_find_ticket_filters to discover valid names")
    identifiers = {identifier for _, _, identifier in exact}
    if len(identifiers) > 1:
        raise HTTPException(409, "More than one IIQ filter has that exact name; choose a more specific taxonomy value")
    raw = await iiq.post_read_query(
        "tickets",
        cid,
        _ticket_query_payload(start, end, body.filter_kind, next(iter(identifiers))),
        {"$s": body.limit, "$p": 0, "$o": "TicketCreatedDate DESC"},
    )
    tickets = [_ticket_summary(record) for record in _ticket_records(raw)][: body.limit]
    return FilteredTicketSearchResponse(
        correlation_id=cid,
        filter_kind=body.filter_kind,
        filter_name=body.filter_name,
        created_after=start,
        created_before=end,
        count=len(tickets),
        tickets=tickets,
    )


@app.post(
    "/tickets/search",
    operation_id="iiq_search_tickets",
    summary="Search tickets by assigned technician and creation date",
    description=(
        "Return compact summaries of authorized tickets assigned to an exact technician display name and created "
        "within an inclusive date window. Uses IIQ's non-mutating ticket-query POST operation; it cannot change tickets."
    ),
    response_model=TicketSearchResponse,
    dependencies=[Depends(require_caller)],
    tags=["tickets"],
)
async def search_tickets(body: TicketSearchRequest, request: Request, response: Response) -> TicketSearchResponse:
    try:
        start, end = body.date_bounds()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    cid = correlation_id(request, response)
    wanted = " ".join(body.assigned_to.split()).casefold()
    user_search = await iiq.post_read_query(
        "search/v2",
        cid,
        {"Query": f'"{body.assigned_to}"', "Facets": 4, "IncludeMatchedItem": False},
    )
    user_ids = _exact_named_ids(user_search, wanted)
    if not user_ids:
        raise HTTPException(404, "No exact Incident IQ user match was found for the assigned technician")
    if len(user_ids) > 1:
        raise HTTPException(409, "More than one exact Incident IQ user match was found; use a unique technician identity")
    agent_id = next(iter(user_ids))

    payload = _ticket_query_payload(start, end, "agent", agent_id)
    raw = await iiq.post_read_query(
        "tickets",
        cid,
        payload,
        {"$s": body.limit, "$p": 0, "$o": "TicketCreatedDate DESC"},
    )
    matches: list[TicketSummary] = []
    for record in _ticket_records(raw):
        summary = _ticket_summary(record)
        summary.assigned_to = body.assigned_to
        matches.append(summary)
    return TicketSearchResponse(
        correlation_id=cid,
        assigned_to=body.assigned_to,
        created_after=start,
        created_before=end,
        count=len(matches),
        tickets=matches[: body.limit],
    )


@app.get(
    "/assets/{asset_id}",
    operation_id="iiq_get_asset",
    summary="Get one Incident IQ asset",
    description="Retrieve one authorized asset by its Incident IQ record identifier. This operation cannot modify the asset.",
    response_model=IIQResult,
    dependencies=[Depends(require_caller)],
    tags=["assets"],
)
async def get_asset(asset_id: str, request: Request, response: Response) -> IIQResult:
    cid = correlation_id(request, response)
    path = f"assets/{safe_segment(asset_id, 'asset ID')}"
    return IIQResult(correlation_id=cid, resource="asset", data=await iiq.get(path, cid))


@app.get(
    "/assets/by-tag/{asset_tag}",
    operation_id="iiq_get_asset_by_tag",
    summary="Find an Incident IQ asset by asset tag",
    description="Retrieve an authorized asset using an exact asset-tag lookup. This operation cannot modify the asset.",
    response_model=IIQResult,
    dependencies=[Depends(require_caller)],
    tags=["assets"],
)
async def get_asset_by_tag(asset_tag: str, request: Request, response: Response) -> IIQResult:
    cid = correlation_id(request, response)
    path = f"assets/assettag/search/{safe_segment(asset_tag, 'asset tag')}"
    return IIQResult(correlation_id=cid, resource="asset", data=await iiq.get(path, cid))


@app.get(
    "/users/{user_id}",
    operation_id="iiq_get_user",
    summary="Get one Incident IQ user",
    description="Retrieve one authorized IIQ user record by identifier. Return only data permitted by the IIQ integration identity.",
    response_model=IIQResult,
    dependencies=[Depends(require_caller)],
    tags=["users"],
)
async def get_user(user_id: str, request: Request, response: Response) -> IIQResult:
    cid = correlation_id(request, response)
    path = f"users/{safe_segment(user_id, 'user ID')}"
    return IIQResult(correlation_id=cid, resource="user", data=await iiq.get(path, cid))


@app.get(
    "/locations",
    operation_id="iiq_list_locations",
    summary="List authorized Incident IQ locations",
    description="Retrieve the IIQ locations visible to the read-only integration identity.",
    response_model=IIQResult,
    dependencies=[Depends(require_caller)],
    tags=["reference"],
)
async def list_locations(request: Request, response: Response) -> IIQResult:
    cid = correlation_id(request, response)
    return IIQResult(correlation_id=cid, resource="locations", data=await iiq.get("locations/all", cid))


@app.post(
    "/advanced-read",
    operation_id="iiq_advanced_read",
    summary="Run an allowlisted Incident IQ GET request",
    description=(
        "Administrator-controlled escape hatch for documented IIQ read endpoints that do not yet have a named tool. "
        "Disabled by default; when enabled, only GET requests under configured resource prefixes are permitted."
    ),
    response_model=IIQResult,
    dependencies=[Depends(require_caller)],
    tags=["advanced"],
)
async def advanced_read(body: AdvancedReadRequest, request: Request, response: Response) -> IIQResult:
    if not settings.iiq_enable_advanced_read:
        raise HTTPException(403, "Advanced read is disabled")
    path = validate_advanced_path(body.relative_path, settings)
    cid = correlation_id(request, response)
    return IIQResult(correlation_id=cid, resource=path.split("/", 1)[0], data=await iiq.get(path, cid, body.query))
