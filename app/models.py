from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class IIQResult(BaseModel):
    correlation_id: str
    resource: str
    data: Any


class AdvancedReadRequest(BaseModel):
    relative_path: str = Field(
        min_length=1,
        max_length=500,
        description="Relative IIQ API path under an administrator-approved resource prefix, without /api/v1.0.",
        examples=["tickets/12345"],
    )
    query: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="Optional scalar query-string parameters sent with the GET request.",
    )

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        candidate = value.strip().lstrip("/")
        if not candidate or ".." in candidate.split("/") or "://" in candidate or "?" in candidate or "#" in candidate:
            raise ValueError("relative_path must be a safe path without a URL, query string, fragment, or parent traversal")
        return candidate


class HealthResponse(BaseModel):
    status: str
    iiq_configured: bool
    mode: str
    advanced_read_enabled: bool


class TicketSearchRequest(BaseModel):
    assigned_to: str = Field(
        min_length=2,
        max_length=150,
        description="Exact display name of the assigned Incident IQ technician.",
        examples=["Alex Technician"],
    )
    created_after: date = Field(description="Inclusive ticket creation start date in YYYY-MM-DD format.")
    created_before: date | None = Field(
        default=None,
        description="Inclusive ticket creation end date in YYYY-MM-DD format; defaults to today.",
    )
    limit: int = Field(default=100, ge=1, le=200, description="Maximum number of matching tickets returned.")

    @field_validator("assigned_to")
    @classmethod
    def normalize_assignee(cls, value: str) -> str:
        return " ".join(value.split())

    def date_bounds(self) -> tuple[date, date]:
        end = self.created_before or date.today()
        if end < self.created_after:
            raise ValueError("created_before cannot be earlier than created_after")
        if end - self.created_after > timedelta(days=93):
            raise ValueError("Ticket searches are limited to a 94-day inclusive window")
        return self.created_after, end


class TicketSummary(BaseModel):
    ticket_id: str | None = None
    ticket_number: str | None = None
    subject: str | None = None
    created_at: str | None = None
    status: str | None = None
    assigned_to: str | None = None
    assigned_team: str | None = None
    location: str | None = None
    priority: str | None = None
    category: str | None = None
    requested_for: str | None = None
    submitted_by: str | None = None


class TicketSearchResponse(BaseModel):
    correlation_id: str
    assigned_to: str
    created_after: date
    created_before: date
    count: int
    tickets: list[TicketSummary]


class RequesterTicketSearchRequest(BaseModel):
    requester: str = Field(
        min_length=2,
        max_length=150,
        description="Exact display name of the person who requested or submitted the Incident IQ ticket.",
        examples=["Jordan Requester"],
    )
    created_after: date | None = Field(
        default=None,
        description="Inclusive creation start date; defaults to the last 30 days.",
    )
    created_before: date | None = Field(
        default=None,
        description="Inclusive creation end date; defaults to today.",
    )
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator("requester")
    @classmethod
    def normalize_requester(cls, value: str) -> str:
        return " ".join(value.split())

    def date_bounds(self) -> tuple[date, date]:
        end = self.created_before or date.today()
        start = self.created_after or (end - timedelta(days=29))
        if end < start:
            raise ValueError("created_before cannot be earlier than created_after")
        if end - start > timedelta(days=93):
            raise ValueError("Requester searches are limited to a 94-day inclusive window")
        return start, end


class RequesterTicketSearchResponse(BaseModel):
    correlation_id: str
    requester: str
    created_after: date
    created_before: date
    count: int
    tickets: list[TicketSummary]


class TicketTimelineEntry(BaseModel):
    timestamp: str | None = None
    actor: str | None = None
    activity_type: str
    is_public: bool | None = None
    text: str | None = None


class TicketTimelineResponse(BaseModel):
    correlation_id: str
    ticket_number: str
    count: int
    entries: list[TicketTimelineEntry]


class TechnicianTicketContextResponse(BaseModel):
    correlation_id: str
    ticket_number: str
    ticket: dict[str, Any]
    timeline_count: int
    timeline: list[TicketTimelineEntry]


TicketFilterKind = Literal["team", "issuecategory", "issuetype", "issue"]


class TicketFilterLookupRequest(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=100,
        description="Words to find in IIQ's ticket teams and category taxonomy.",
        examples=["Google", "SUPPORT TEAM"],
    )
    kinds: list[TicketFilterKind] = Field(
        default_factory=lambda: ["team", "issuecategory", "issuetype", "issue"],
        min_length=1,
        max_length=4,
        description="IIQ filter types to search. Use team for assignment teams; use the other types for ticket taxonomy.",
    )
    limit: int = Field(default=25, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())


class TicketFilterCandidate(BaseModel):
    kind: TicketFilterKind
    name: str


class TicketFilterLookupResponse(BaseModel):
    correlation_id: str
    query: str
    count: int
    matches: list[TicketFilterCandidate]


class FilteredTicketSearchRequest(BaseModel):
    filter_kind: TicketFilterKind = Field(
        description="Exact IIQ filter type returned by iiq_find_ticket_filters."
    )
    filter_name: str = Field(
        min_length=2,
        max_length=250,
        description="Exact IIQ team or taxonomy name returned by iiq_find_ticket_filters.",
        examples=["SUPPORT TEAM", "Google Apps for Education"],
    )
    created_after: date = Field(description="Inclusive ticket creation start date in YYYY-MM-DD format.")
    created_before: date | None = Field(
        default=None,
        description="Inclusive ticket creation end date in YYYY-MM-DD format; defaults to today.",
    )
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator("filter_name")
    @classmethod
    def normalize_filter_name(cls, value: str) -> str:
        return " ".join(value.split())

    def date_bounds(self) -> tuple[date, date]:
        end = self.created_before or date.today()
        if end < self.created_after:
            raise ValueError("created_before cannot be earlier than created_after")
        if end - self.created_after > timedelta(days=93):
            raise ValueError("Ticket searches are limited to a 94-day inclusive window")
        return self.created_after, end


class FilteredTicketSearchResponse(BaseModel):
    correlation_id: str
    filter_kind: TicketFilterKind
    filter_name: str
    created_after: date
    created_before: date
    count: int
    tickets: list[TicketSummary]
