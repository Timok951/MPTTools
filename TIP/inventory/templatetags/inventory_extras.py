from django import template

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
    return user.is_superuser or user.groups.filter(name=group_name).exists()


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

