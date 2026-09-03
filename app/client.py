from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from .config import Settings

logger = logging.getLogger("mcp_iiq.audit")


def safe_segment(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 200 or cleaned in {".", ".."}:
        raise HTTPException(400, f"Invalid {label}")
    return quote(cleaned, safe="")


def validate_advanced_path(path: str, settings: Settings) -> str:
    normalized = path.strip().lstrip("/")
    parts = normalized.split("/")
    if not normalized or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(400, "Invalid relative IIQ path")
    if "://" in normalized or "?" in normalized or "#" in normalized:
        raise HTTPException(400, "The advanced path cannot contain a URL, query string, or fragment")
    if parts[0].lower() not in settings.allowed_read_prefixes:
        raise HTTPException(403, "Resource prefix is not allowlisted")
    return "/".join(quote(part, safe="-._~") for part in parts)


class IIQClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _headers(self, correlation_id: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.settings.iiq_api_token.get_secret_value()}",
            "Client": self.settings.iiq_client_header,
            "X-Correlation-ID": correlation_id,
        }
        if self.settings.iiq_site_id:
            headers["SiteId"] = self.settings.iiq_site_id
        if self.settings.iiq_product_id:
            headers["ProductId"] = self.settings.iiq_product_id
        return headers

    async def get(self, relative_path: str, correlation_id: str, query: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", relative_path, correlation_id, query=query)

    async def post_read_query(
        self,
        relative_path: str,
        correlation_id: str,
        body: dict[str, Any],
        query: dict[str, Any] | None = None,
    ) -> Any:
        """Issue POST only for a fixed, non-mutating IIQ search endpoint."""
        if relative_path.strip("/").lower() not in {"tickets", "assets", "search", "search/v2", "filters"}:
            raise HTTPException(500, "POST read queries are restricted to approved search endpoints")
        return await self._request("POST", relative_path, correlation_id, query=query, body=body)

    async def _request(
        self,
        method: str,
        relative_path: str,
        correlation_id: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if not self.settings.iiq_is_configured:
            raise HTTPException(503, "Incident IQ connection is not configured")

        url = f"{self.settings.api_root}/{relative_path.lstrip('/')}"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.iiq_timeout_seconds,
                verify=self.settings.iiq_verify_ssl,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.request(method, url, headers=self._headers(correlation_id), params=query, json=body)
        except httpx.TimeoutException as exc:
            logger.warning("iiq_get_timeout correlation_id=%s resource=%s", correlation_id, relative_path.split("/", 1)[0])
            raise HTTPException(504, "Incident IQ request timed out") from exc
        except httpx.RequestError as exc:
            logger.error("iiq_get_failed correlation_id=%s resource=%s error_type=%s", correlation_id, relative_path.split("/", 1)[0], type(exc).__name__)
            raise HTTPException(502, "Incident IQ could not be reached") from exc

        elapsed_ms = round((time.monotonic() - started) * 1000)
        logger.info(
            "iiq_read correlation_id=%s method=%s resource=%s status=%s elapsed_ms=%s bytes=%s",
            correlation_id,
            method,
            relative_path.split("/", 1)[0],
            response.status_code,
            elapsed_ms,
            len(response.content),
        )

        if len(response.content) > self.settings.iiq_max_response_bytes:
            raise HTTPException(502, "Incident IQ response exceeded the configured size limit")
        if response.status_code == 401 or response.status_code == 403:
            raise HTTPException(502, "Incident IQ rejected the integration credential or its permissions")
        if response.status_code == 404:
            raise HTTPException(404, "Incident IQ record was not found")
        if response.status_code >= 400:
            raise HTTPException(502, f"Incident IQ returned HTTP {response.status_code}")

        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(502, "Incident IQ returned a non-JSON response") from exc
