"""Runtime configuration, loaded from environment variables (prefix ``MUSE2API_``) and ``.env``."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DriverName = Literal["mock", "browser", "http"]
PoolStrategyName = Literal["round_robin", "lru", "affinity"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MUSE2API_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 18610
    log_level: str = "INFO"
    public_base: str = Field(
        default="",
        description="Externally reachable base URL used when building media links. "
        "Empty means derive from the incoming request.",
    )

    # --- auth ---
    api_key: str = Field(default="", description="Bearer key for /v1/*. Auto-generated if empty.")
    admin_key: str = Field(default="", description="Bearer key for /admin/*. Falls back to api_key.")
    dashboard_password: str = Field(
        default="201407@Hva",
        description="Password for the dashboard sign-in page. Empty disables password sign-in; "
        "the admin key still works. Set MUSE2API_DASHBOARD_PASSWORD to change it.",
    )

    # --- storage ---
    data_dir: Path = Path("data")
    request_log_retention_days: float = Field(
        default=14, description="Days of /v1/* request history kept in data/requests.db."
    )
    media_retention_days: float = Field(
        default=14, description="Days generated media files are kept in data/media. "
        "0 disables automatic cleanup."
    )
    quota_history_retention_days: float = Field(
        default=30, description="Days of weekly-quota samples kept in data/quota.db for the "
        "dashboard trend and the burn-rate forecast."
    )

    # --- upstream driver ---
    driver: DriverName = "mock"
    site_url: str = "https://muse.ai"

    # browser driver
    chromium_path: str = ""
    cdp_port: int = 19210
    headless: bool = True
    browser_proxy: str = Field(
        default="",
        description="Proxy for the browser only, e.g. socks5://127.0.0.1:10808 or http://10.144.1.10:8080. "
        "Python's own calls (session renewal) follow http_proxy/https_proxy instead.",
    )
    browser_profile_dir: Path | None = None
    chromium_flags: str = Field(
        default="",
        description="Extra Chromium launch flags, space-separated, appended after the "
        "built-in ones (e.g. --no-sandbox when the kernel blocks the sandbox).",
    )
    page_ready_timeout: float = 45.0
    warm_threads: int = Field(
        default=2,
        description="Pre-started muse.ai threads kept per account. A new thread takes muse.ai "
        "20-40s to answer its first message, a started one about 5s, so a chat that opens a new "
        "thread goes into a warm one instead. Each costs one extra short message of quota. "
        "Needs MUSE2API_ACCOUNT_MAX_CONCURRENCY of at least 2; 0 disables.",
    )
    warm_prompt: str = Field(default="Hi", description="Message that starts a warm thread.")

    # --- timeouts (seconds) ---
    chat_timeout: float = 300.0
    first_token_timeout: float = 75.0
    stream_heartbeat_interval: float = Field(
        default=0.0,
        description="SSE ': keepalive' comment interval while waiting for the first "
        "stream token; 0 disables. Keeps idle clients/proxies from timing out.",
    )
    image_timeout: float = 240.0
    video_timeout: float = 600.0

    # --- account pool ---
    pool_strategy: PoolStrategyName = "affinity"
    pool_acquire_timeout: float = 60.0
    account_max_concurrency: int = 4
    account_cooldown: float = 120.0
    max_failover: int = 2
    affinity_ttl: float = Field(
        default=3600,
        description="How long a conversation stays pinned to the account that served it "
        "(affinity strategy); a hit renews the binding. 0 disables expiry.",
    )
    quota_low_threshold_pct: float = Field(
        default=85,
        description="Weekly quota percentage above which an account is only given "
        "conversations already bound to it; 0 disables the check.",
    )

    # --- diagnostics ---
    debug_capture: bool = Field(
        default=False,
        description="On a browser-driver failure, save a screenshot, the page URL and the "
        "page text under data/debug/<id>/ so a broken selector can be fixed from evidence. "
        "Off by default: a capture can contain account content.",
    )
    debug_capture_max_dirs: int = Field(
        default=50, description="Most failure captures kept under data/debug."
    )
    debug_capture_retention_days: float = Field(
        default=3, description="Days a failure capture is kept under data/debug."
    )

    # --- background removal (images with background="transparent") ---
    matting_model: str = Field(
        default="birefnet-general",
        description="rembg model used to cut out the subject, e.g. birefnet-general "
        "(best) or birefnet-general-lite (faster).",
    )

    # --- keepalive (session renewal) ---
    keepalive_enabled: bool = True
    keepalive_interval: float = 600

    @field_validator("browser_profile_dir", mode="before")
    @classmethod
    def _blank_profile_dir_is_unset(cls, value: object) -> object:
        """``.env.example`` ships ``MUSE2API_BROWSER_PROFILE_DIR=``; ``Path("")`` would be the
        working directory, so Chrome would scatter its profile across the repo root."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def accounts_file(self) -> Path:
        return self.data_dir / "accounts.json"

    @property
    def tasks_file(self) -> Path:
        return self.data_dir / "tasks.json"

    @property
    def key_file(self) -> Path:
        return self.data_dir / "api_key"

    @property
    def keys_file(self) -> Path:
        return self.data_dir / "keys.json"

    @property
    def requests_db(self) -> Path:
        return self.data_dir / "requests.db"

    @property
    def debug_dir(self) -> Path:
        return self.data_dir / "debug"

    @property
    def quota_db(self) -> Path:
        return self.data_dir / "quota.db"

    @property
    def profile_dir(self) -> Path:
        return self.browser_profile_dir or (self.data_dir / "browser-profile")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.media_dir):
            d.mkdir(parents=True, exist_ok=True)

    def resolve_api_key(self) -> str:
        """Return the configured key, or load/generate a persistent one under ``data_dir``."""
        if self.api_key:
            return self.api_key
        self.ensure_dirs()
        if self.key_file.is_file():
            key = self.key_file.read_text(encoding="utf-8").strip()
            if key:
                self.api_key = key
                return key
        key = "m2a-" + secrets.token_urlsafe(24)
        self.key_file.write_text(key + "\n", encoding="utf-8")
        self.key_file.chmod(0o600)
        self.api_key = key
        return key

    @property
    def effective_admin_key(self) -> str:
        return self.admin_key or self.api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
