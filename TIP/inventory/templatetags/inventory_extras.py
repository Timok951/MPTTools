from django import template
from inventory.authz import ROLE_ALIASES, user_has_capability

register = template.Library()


@register.filter
def get_item(mapping, key):
    if not mapping:
        return []
    return mapping.get(key, [])


@register.filter
def has_group(user, group_name: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    allowed_names = {group_name}
    for canonical_name, aliases in ROLE_ALIASES.items():
        if group_name == canonical_name or group_name in aliases:
            allowed_names.add(canonical_name)
            allowed_names.update(aliases)
    return user.groups.filter(name__in=allowed_names).exists()


@register.filter
def has_capability(user, capability: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user_has_capability(user, capability)


@register.filter
def dict_get(mapping, key):
    if not isinstance(mapping, dict):
        return key
    return mapping.get(key, key)


@register.filter
def get_attr(obj, name: str):
    if obj is None or not name:
        return ""
    attr = getattr(obj, name, None)
    if attr is None:
        return ""
    if callable(attr):
        try:
            return attr()
        except TypeError:
            return attr
    return attr


@register.simple_tag(takes_context=True)
def querystring(context, **kwargs):
    request = context.get("request")
    if request is None:
        return ""
    query = request.GET.copy()
    for key, value in kwargs.items():
        if value in ("", None):
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()


@register.simple_tag(takes_context=True)
def ui_text(context, ru_text: str, en_text: str):
    request = context.get("request")
    language_code = getattr(request, "LANGUAGE_CODE", "") if request else ""
    if str(language_code).lower().startswith("en"):
        return en_text
    return ru_text

