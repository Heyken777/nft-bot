from django import template
register = template.Library()

@register.filter
def dictget(d, key):
    if isinstance(d, dict):
        return d.get(key, 0)
    return 0

@register.filter
def get_range(value):
    return range(1, value + 1)