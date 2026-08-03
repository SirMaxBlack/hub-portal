import sys
from copy import copy as shallow_copy

from django.apps import AppConfig


def patch_django_template_context_copy() -> None:
    if sys.version_info < (3, 14):
        return

    from django.template.context import BaseContext, Context

    try:
        shallow_copy(BaseContext())
        return
    except AttributeError:
        pass

    def _copy_slots(source, target) -> None:
        for cls in type(source).__mro__:
            slots = getattr(cls, "__slots__", ())
            if isinstance(slots, str):
                slots = (slots,)
            for slot in slots:
                if slot in {"__dict__", "__weakref__"}:
                    continue
                if hasattr(source, slot):
                    setattr(target, slot, getattr(source, slot))

    def _base_context_copy(self):
        duplicate = self.__class__.__new__(self.__class__)
        if hasattr(self, "__dict__"):
            duplicate.__dict__ = self.__dict__.copy()
        _copy_slots(self, duplicate)
        duplicate.dicts = self.dicts[:]
        return duplicate

    def _context_copy(self):
        duplicate = _base_context_copy(self)
        duplicate.render_context = shallow_copy(self.render_context)
        return duplicate

    BaseContext.__copy__ = _base_context_copy
    Context.__copy__ = _context_copy


class PortalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "portal"

    def ready(self):
        patch_django_template_context_copy()

        from django.db.models.signals import m2m_changed

        from .access_control import mark_user_access_state_updated
        from .models import User

        def handle_user_services_changed(sender, instance, action, **kwargs):
            if action in {"post_add", "post_remove", "post_clear"}:
                mark_user_access_state_updated(instance)

        m2m_changed.connect(
            handle_user_services_changed,
            sender=User.services.through,
            dispatch_uid="portal.user-services-access-state",
            weak=False,
        )
