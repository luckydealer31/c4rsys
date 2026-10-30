from django.urls import path

from c4rsys.exports import views

urlpatterns = [
    path("c/<str:cmp>/exports", views.export_list),
    path("c/<str:cmp>/exports/<uuid:export_id>/download", views.export_download),
]
