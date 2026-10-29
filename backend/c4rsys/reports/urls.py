from django.urls import path

from c4rsys.reports import views

urlpatterns = [
    path("c/<str:cmp>/reports/personal", views.personal_list),
    path("c/<str:cmp>/reports/personal/<uuid:dashboard_id>", views.personal_detail),
    path("c/<str:cmp>/reports/<str:code>", views.dashboard),
    path("c/<str:cmp>/reports/<str:code>/xlsx", views.dashboard_xlsx),
]
