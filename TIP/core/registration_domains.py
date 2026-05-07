"""Разрешённые домены почты для регистрации и восстановления пароля (настраиваются в админке)."""

from __future__ import annotations

from django.core.exceptions import ValidationError

# Если в БД нет ни одной активной записи, используется этот домен (обратная совместимость).
DEFAULT_REGISTRATION_EMAIL_FALLBACK = "mpt.ru"


def get_registration_email_domains() -> list[str]:
    from core.models import RegistrationAllowedEmailDomain

    active = list(
        RegistrationAllowedEmailDomain.objects.filter(is_active=True)
        .values_list("domain", flat=True)
        .order_by("domain")
    )
    if active:
        return active
    if RegistrationAllowedEmailDomain.objects.exists():
        return []
    return [DEFAULT_REGISTRATION_EMAIL_FALLBACK]


def registration_domains_display() -> str:
    domains = get_registration_email_domains()
    if not domains:
        return "(не настроено)"
    return ", ".join(f"@{d}" for d in domains)


def email_allowed_for_registration(email: str) -> bool:
    normalized = (email or "").strip().lower()
    if "@" not in normalized:
        return False
    host = normalized.rsplit("@", 1)[-1]
    allowed = [d.lower() for d in get_registration_email_domains()]
    if not allowed:
        return False
    return host in allowed


def validate_corporate_registration_email(email: str) -> str:
    domains = get_registration_email_domains()
    if not domains:
        raise ValidationError(
            "Регистрация по email временно недоступна: не включён ни один разрешённый домен. Обратитесь к администратору."
        )
    if not email_allowed_for_registration(email):
        listed = ", ".join(f"@{d}" for d in domains)
        raise ValidationError(f"Разрешены адреса только на доменах: {listed}.")
    return email


def registration_email_placeholder() -> str:
    domains = get_registration_email_domains()
    if not domains:
        return "email@example.com"
    first = domains[0]
    return f"user@{first}"
