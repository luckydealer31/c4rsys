from django.urls import path

from c4rsys.platform.views import health

urlpatterns = [
    path("api/health", health, name="health"),
]
