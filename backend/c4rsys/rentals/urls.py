from django.urls import path

from c4rsys.rentals import views

urlpatterns = [
    path("c/<str:cmp>/tariffs", views.tariff_list),
    path("c/<str:cmp>/tariffs/<uuid:tariff_id>", views.tariff_detail),
    path("c/<str:cmp>/tariffs/<uuid:tariff_id>/archive", views.tariff_archive),
    path("c/<str:cmp>/tariffs/<uuid:tariff_id>/restore", views.tariff_restore),
]
