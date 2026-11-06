"""Service container wired once at startup and stored on ``app.state.services``."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..accounts.pool import AccountPool
from ..accounts.store import JsonAccountStore
from ..auth.keys import KeyStore
from ..config import Settings
from ..core.matting import Matting
from ..core.media import MediaStore
from ..drivers.base import MuseDriver
from ..drivers.registry import create_driver
from .active import ActiveRequests
from .gateway import Gateway
from .quota_history import QuotaHistory
from .request_log import RequestLog, current_record
from .sessions import SessionStore
from .tasks import Task, TaskManager

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    driver: MuseDriver
    pool: AccountPool
    gateway: Gateway
    tasks: TaskManager
    media: MediaStore
    matting: Matting
    keys: KeyStore
    requests: RequestLog
    active: ActiveRequests
    quota: QuotaHistory
    sessions: SessionStore

    async def apply_runtime_config(self, changes: dict[str, Any]) -> None:
        """Push settings the app reads live into the objects that cached them."""
        pool_keys = {"account_cooldown", "account_max_concurrency", "affinity_ttl",
                     "quota_low_threshold_pct"}
        if pool_keys & changes.keys():
            await self.pool.reconfigure(
                cooldown=self.settings.account_cooldown,
                max_concurrency=self.settings.account_max_concurrency,
                affinity_ttl=self.settings.affinity_ttl,
                quota_low_threshold_pct=self.settings.quota_low_threshold_pct,
            )
        if "debug_capture" in changes:
            evidence = getattr(self.driver, "evidence", None)
            if evidence is not None:
                evidence.enabled = self.settings.debug_capture
        log.info("runtime config updated: %s", ", ".join(sorted(changes)))

    @classmethod
    def build(cls, settings: Settings, driver: MuseDriver | None = None) -> Services:
        settings.ensure_dirs()
        driver = driver or create_driver(settings)
        pool = AccountPool(
            JsonAccountStore(settings.accounts_file),
            strategy=settings.pool_strategy,
            max_concurrency=settings.account_max_concurrency,
            cooldown=settings.account_cooldown,
            acquire_timeout=settings.pool_acquire_timeout,
            affinity_ttl=settings.affinity_ttl,
            quota_low_threshold_pct=settings.quota_low_threshold_pct,
        )
        requests = RequestLog(settings.requests_db)

        async def record_outcome(task: Task) -> None:
            record = current_record.get()
            await requests.finish_task(task.id, task.status.value, task.updated_at,
                                       (task.error or {}).get("message"),
                                       record.get("account_id") if record else None)

        return cls(
            settings=settings,
            driver=driver,
            pool=pool,
            gateway=Gateway(pool, driver, max_failover=settings.max_failover),
            tasks=TaskManager(settings.tasks_file, on_finish=record_outcome),
            media=MediaStore(settings.media_dir),
            matting=Matting(settings.matting_model),
            keys=KeyStore(settings.keys_file),
            requests=requests,
            active=ActiveRequests(),
            quota=QuotaHistory(settings.quota_db),
            sessions=SessionStore(),
        )
