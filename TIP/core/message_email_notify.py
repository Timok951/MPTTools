"""E-mail-уведомления о новых сообщениях (личные и по заявкам)."""

from __future__ import annotations

import logging
from html import escape as html_escape

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email

from core.mail_out import send_multipart_email
from operations.models import REQUEST_APPROVED, REQUEST_STATUS_CHOICES

logger = logging.getLogger(__name__)


def _public_base_url() -> str:
    return (getattr(settings, "PUBLIC_SITE_URL", None) or "").strip().rstrip("/")


def _abs_link(path: str, label: str) -> tuple[str, str]:
    """Пара (фрагмент plain, фрагмент html) для ссылки."""
    path = (path or "").strip()
    if not path.startswith("/"):
        path = "/" + path
    base = _public_base_url()
    if not base:
        return ("", "")
    url = f"{base}{path}"
    return (f"{label}: {url}\n", f'<p><a href="{html_escape(url)}">{html_escape(label)}</a></p>')


def _truncate(text: str, max_len: int = 800) -> str:
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _send_to_user(*, user: User, subject: str, plain_body: str, html_body: str) -> None:
    to = (getattr(user, "email", None) or "").strip()
    if not to:
        logger.info("Пропуск e-mail «%s»: у пользователя id=%s не заполнено поле email", subject, user.pk)
        return
    try:
        validate_email(to)
    except DjangoValidationError:
        logger.info(
            "Пропуск e-mail «%s»: у пользователя id=%s некорректный адрес «%s»",
            subject,
            user.pk,
            to,
        )
        return
    send_multipart_email(subject=subject, plain_body=plain_body, html_body=html_body, to=[to])


def notify_direct_message_email(
    recipient: User,
    *,
    sender_username: str,
    sender_id: int,
    body: str,
) -> None:
    """Письмо получателю личного сообщения."""
    if not getattr(settings, "MESSAGE_EMAIL_ENABLED", True):
        return
    preview = _truncate(body, 1200)
    subj = "Новое личное сообщение в системе"
    plain_link, html_link = _abs_link(f"/messages/?user={sender_id}", "Открыть диалог в системе")
    plain = (
        f"Здравствуйте!\n\n"
        f"Вам написал пользователь «{sender_username}»:\n\n"
        f"{preview}\n\n"
        f"{plain_link}"
    )
    safe_user = html_escape(sender_username)
    safe_preview = html_escape(preview).replace("\n", "<br/>")
    html = (
        "<!DOCTYPE html><html><body style=\"font-family:system-ui,sans-serif;line-height:1.5;color:#1f2a44;\">"
        "<p>Здравствуйте!</p>"
        f"<p>Вам написал пользователь <strong>{safe_user}</strong>:</p>"
        f"<blockquote style=\"border-left:3px solid #4c67ff;padding-left:12px;margin:12px 0;\">{safe_preview}</blockquote>"
        f"{html_link}"
        "</body></html>"
    )
    try:
        _send_to_user(user=recipient, subject=subj, plain_body=plain, html_body=html)
    except Exception:
        logger.exception("Не удалось отправить e-mail о личном сообщении получателю %s", recipient.pk)


def _is_automated_status_change_to_approved(body: str) -> bool:
    """Сообщение из UI при смене статуса на «Одобрена» — для него отдельное письмо об одобрении."""
    approved_label = dict(REQUEST_STATUS_CHOICES)[REQUEST_APPROVED]
    b = (body or "").strip()
    return b.startswith("Статус изменён:") and f"-> {approved_label}" in b


def notify_request_approved_email(
    recipient: User,
    *,
    request_id: int,
    approver_username: str | None,
) -> None:
    """Письмо заявителю при переходе заявки в статус «Одобрена»."""
    if not getattr(settings, "MESSAGE_EMAIL_ENABLED", True):
        return
    subj = f"Заявка #{request_id} одобрена"
    approver_line = (
        f"Одобрил: «{approver_username}».\n\n"
        if approver_username
        else ""
    )
    safe_approver = html_escape(approver_username) if approver_username else ""
    plain_link, html_link = _abs_link(f"/requests/{request_id}/", "Открыть заявку")
    plain = (
        "Здравствуйте!\n\n"
        f"Ваша заявка #{request_id} одобрена.\n\n"
        f"{approver_line}"
        f"{plain_link}"
    )
    html_approver = (
        f"<p>Одобрил: <strong>{safe_approver}</strong>.</p>"
        if approver_username
        else ""
    )
    html = (
        "<!DOCTYPE html><html><body style=\"font-family:system-ui,sans-serif;line-height:1.5;color:#1f2a44;\">"
        "<p>Здравствуйте!</p>"
        f"<p>Ваша заявка <strong>#{request_id}</strong> одобрена.</p>"
        f"{html_approver}"
        f"{html_link}"
        "</body></html>"
    )
    try:
        _send_to_user(user=recipient, subject=subj, plain_body=plain, html_body=html)
    except Exception:
        logger.exception(
            "Не удалось отправить e-mail об одобрении заявки #%s получателю %s",
            request_id,
            recipient.pk,
        )


def notify_request_message_email(
    recipient: User,
    *,
    request_id: int,
    author_username: str,
    body: str,
) -> None:
    """Письмо участнику заявки о новом комментарии (кроме автора комментария)."""
    if not getattr(settings, "MESSAGE_EMAIL_ENABLED", True):
        return
    preview = _truncate(body, 1200)
    subj = f"Новое сообщение по заявке #{request_id}"
    plain_link, html_link = _abs_link(f"/requests/{request_id}/", "Открыть заявку")
    plain = (
        f"Здравствуйте!\n\n"
        f"По заявке #{request_id} пользователь «{author_username}» оставил сообщение:\n\n"
        f"{preview}\n\n"
        f"{plain_link}"
    )
    safe_author = html_escape(author_username)
    safe_preview = html_escape(preview).replace("\n", "<br/>")
    html = (
        "<!DOCTYPE html><html><body style=\"font-family:system-ui,sans-serif;line-height:1.5;color:#1f2a44;\">"
        "<p>Здравствуйте!</p>"
        f"<p>По заявке <strong>#{request_id}</strong> пользователь <strong>{safe_author}</strong> оставил сообщение:</p>"
        f"<blockquote style=\"border-left:3px solid #4c67ff;padding-left:12px;margin:12px 0;\">{safe_preview}</blockquote>"
        f"{html_link}"
        "</body></html>"
    )
    try:
        _send_to_user(user=recipient, subject=subj, plain_body=plain, html_body=html)
    except Exception:
        logger.exception(
            "Не удалось отправить e-mail о сообщении по заявке #%s получателю %s",
            request_id,
            recipient.pk,
        )


def notify_request_message_subscribers(
    *,
    request_id: int,
    author_id: int,
    author_username: str,
    body: str,
    requester: User | None,
    processed_by: User | None,
    automation_body: str | None = None,
) -> None:
    """Уведомить заявителя и обработчика (если есть), исключая автора сообщения."""
    skip_automation_body = automation_body if automation_body is not None else body
    seen: set[int] = set()
    for user in (requester, processed_by):
        if user is None or user.pk == author_id or user.pk in seen:
            continue
        if (
            requester is not None
            and user.pk == requester.pk
            and _is_automated_status_change_to_approved(skip_automation_body)
        ):
            continue
        seen.add(user.pk)
        notify_request_message_email(
            user,
            request_id=request_id,
            author_username=author_username,
            body=body,
        )
