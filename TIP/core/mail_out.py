"""Исходящие письма сайта. Использует EMAIL_BACKEND из settings (SMTP / Gmail / Anymail и т.д.)."""

from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives


def send_multipart_email(
    *,
    subject: str,
    plain_body: str,
    html_body: str,
    to: list[str],
    fail_silently: bool = False,
) -> None:
    """Текст + HTML одним сообщением на указанные адреса."""
    recipients = [a.strip() for a in to if (a or "").strip()]
    if not recipients:
        return
    from_email = (getattr(settings, "DEFAULT_FROM_EMAIL", None) or "").strip() or "noreply@localhost"
    msg = EmailMultiAlternatives(subject, plain_body, from_email, recipients)
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=fail_silently)
