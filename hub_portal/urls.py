from django.contrib import admin
from django.urls import include, path


admin.site.site_header = "HUB Portal"
admin.site.site_title = "HUB Portal"
admin.site.index_title = "Управление"


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include(("portal.urls", "portal"), namespace="portal")),
]
