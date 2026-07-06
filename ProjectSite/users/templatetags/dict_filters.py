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

@register.filter
def ru_plural(value, forms):
    """Russian plural: {{ count|ru_plural:'сделка,сделки,сделок' }}"""
    try:
        n = abs(int(value))
        forms_list = forms.split(',')
        if len(forms_list) != 3:
            return str(value)
        if n % 10 == 1 and n % 100 != 11:
            return f"{value} {forms_list[0]}"
        elif n % 10 in [2,3,4] and n % 100 not in [12,13,14]:
            return f"{value} {forms_list[1]}"
        else:
            return f"{value} {forms_list[2]}"
    except (ValueError, TypeError):
        return str(value)