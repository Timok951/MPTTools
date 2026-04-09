import logging

from django.http import HttpRequest

from .models import AdminPortalLog

logger = logging.getLogger("inventory.portal")


def log_portal_action(request: HttpRequest, action: str, entity_slug: str, obj=None, meta=None) -> None:
    if not request.user.is_authenticated:
        return
    object_repr = str(obj)[:200] if obj is not None else ""
    AdminPortalLog.objects.create(
        actor=request.user,
        action=action,
        entity_slug=entity_slug,
        object_repr=object_repr,
        path=request.path[:500],
        meta=meta or {},
    )
    logger.info(
        "portal action=%s entity=%s user=%s repr=%s path=%s",
        action,
        entity_slug,
        request.user.get_username(),
        object_repr,
        request.path,
    )
