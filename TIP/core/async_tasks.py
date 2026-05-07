from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import close_old_connections

logger = logging.getLogger(__name__)

_MAX_WORKERS = max(1, int(getattr(settings, "EMAIL_BACKGROUND_WORKERS", 2) or 2))
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="mail-bg")


def run_in_background(func, *args, **kwargs) -> None:
    """
    Run a callable outside request thread.
    Used as lightweight async bridge until proper task queue is enabled.
    """

    def _wrapped():
        close_old_connections()
        try:
            func(*args, **kwargs)
        except Exception:
            logger.exception("Background task failed: %s", getattr(func, "__name__", "callable"))
        finally:
            close_old_connections()

    _EXECUTOR.submit(_wrapped)
