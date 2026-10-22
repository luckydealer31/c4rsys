from django.urls import include, path

from c4rsys.platform.views import health

urlpatterns = [
    path("api/health", health, name="health"),
    path("api/", include("c4rsys.accounts.urls")),
    path("api/", include("c4rsys.companies.urls")),
]
