from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    iiq_base_url: str = ""
    iiq_api_token: SecretStr = SecretStr("")
    iiq_site_id: str = ""
    iiq_product_id: str = ""
    iiq_client_header: str = "ApiClient"
    api_access_token: SecretStr = SecretStr("")
    iiq_timeout_seconds: float = 20.0
    iiq_max_response_bytes: int = 5_000_000
    iiq_export_max_rows: int = 25_000
    iiq_export_page_size: int = 200
    iiq_report_ttl_seconds: int = 900
    iiq_public_base_url: str = ""
    iiq_verify_ssl: bool = True
    iiq_enable_advanced_read: bool = False
    iiq_allowed_read_prefixes: str = "tickets,assets,users,locations,categories,issues,products"

    @property
    def api_root(self) -> str:
        base = self.iiq_base_url.strip().rstrip("/")
        if base.endswith("/api/v1.0"):
            return base
        return f"{base}/api/v1.0" if base else ""

    @property
    def allowed_read_prefixes(self) -> frozenset[str]:
        return frozenset(value.strip().lower() for value in self.iiq_allowed_read_prefixes.split(",") if value.strip())

    @property
    def iiq_is_configured(self) -> bool:
        return bool(self.api_root and self.iiq_api_token.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
