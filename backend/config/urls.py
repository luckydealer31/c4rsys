from django.urls import include, path

from c4rsys.platform.views import health

urlpatterns = [
    path("api/health", health, name="health"),
    path("api/", include("c4rsys.accounts.urls")),
    path("api/", include("c4rsys.files.urls")),
    path("api/", include("c4rsys.companies.urls")),
    path("api/", include("c4rsys.finance.urls")),
    path("api/", include("c4rsys.fleet.urls")),
    path("api/", include("c4rsys.parties.urls")),
    path("api/", include("c4rsys.orders.urls")),
    path("api/", include("c4rsys.rentals.urls")),
    path("api/", include("c4rsys.warehouse.urls")),
    path("api/", include("c4rsys.service.urls")),
    path("api/", include("c4rsys.delivery.urls")),
    path("api/", include("c4rsys.tasks.urls")),
    path("api/", include("c4rsys.notifications.urls")),
    path("api/", include("c4rsys.reports.urls")),
    path("api/", include("c4rsys.documents.urls")),
    path("api/", include("c4rsys.cabinet.urls")),
]
