from urllib.parse import urlencode

from django import template


register = template.Library()


@register.simple_tag
def service_letter(service):
    return (service.title[:1] or "?").upper()


@register.simple_tag(takes_context=True)
def querystring(context, **kwargs):
    request = context["request"]
    params = request.GET.copy()
    for key, value in kwargs.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else ""


@register.filter
def result_label(value):
    labels = {
        "success": "Успешно",
        "failure": "Ошибка",
        "denied": "Отказано",
        "blocked": "Заблокировано",
        "unauthenticated": "Не авторизован",
        "password_change_required": "Требуется смена пароля",
        "missing": "Нет данных",
        "revoked": "Отозвано",
    }
    return labels.get(value, value or "—")


@register.filter
def auth_method_label(value):
    labels = {
        "account": "Учётная запись",
        "token": "Токен",
    }
    return labels.get(value, value or "—")
