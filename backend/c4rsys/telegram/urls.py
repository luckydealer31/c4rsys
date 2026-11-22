from django.urls import path

from c4rsys.telegram import views

urlpatterns = [
    path("telegram/webhook", views.webhook),
    path("telegram/webapp/session", views.webapp_session),
    path("me/telegram", views.me_telegram),
    path("me/telegram/link", views.me_telegram_link),
    path("me/telegram/unlink", views.me_telegram_unlink),
]
