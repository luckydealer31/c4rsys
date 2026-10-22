from django.urls import path

from c4rsys.rentals import views

urlpatterns = [
    path("c/<str:cmp>/tariffs", views.tariff_list),
    path("c/<str:cmp>/tariffs/<uuid:tariff_id>", views.tariff_detail),
    path("c/<str:cmp>/tariffs/<uuid:tariff_id>/archive", views.tariff_archive),
    path("c/<str:cmp>/tariffs/<uuid:tariff_id>/restore", views.tariff_restore),
    path("c/<str:cmp>/tariffs/<uuid:tariff_id>/vehicles", views.tariff_vehicles),
    path(
        "c/<str:cmp>/tariffs/<uuid:tariff_id>/vehicles/<uuid:card_id>",
        views.tariff_vehicle_terms,
    ),
    path("c/<str:cmp>/rentals", views.rental_list),
    path("c/<str:cmp>/rentals/issue", views.rental_issue),
    path("c/<str:cmp>/rentals/<uuid:rental_id>", views.rental_detail),
]
