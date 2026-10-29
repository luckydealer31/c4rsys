from django.urls import path

from c4rsys.notifications import views

urlpatterns = [
    path("notifications", views.notification_list),
    path("notifications/count", views.notification_count),
    path("notifications/read", views.notification_read),
    path("notifications/<uuid:recipient_id>", views.notification_detail),
    path("c/<str:cmp>/notification-rules", views.rule_list),
    path("c/<str:cmp>/notification-rules/change", views.rule_change),
]
