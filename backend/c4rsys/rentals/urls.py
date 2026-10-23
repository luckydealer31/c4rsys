from django.urls import path

from c4rsys.rentals import views

urlpatterns = [
    path("c/<str:cmp>/readiness", views.company_readiness),
    path("c/<str:cmp>/setup/complete", views.company_setup_complete),
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
    path("c/<str:cmp>/rentals/quote", views.rental_quote),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/tariffs", views.vehicle_tariffs),
    path("c/<str:cmp>/rentals/<uuid:rental_id>", views.rental_detail),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/return", views.rental_return),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/history", views.rental_history),
    path(
        "c/<str:cmp>/rentals/<uuid:rental_id>/complete-formalization",
        views.rental_complete_formalization,
    ),
    path("c/<str:cmp>/inspections/<uuid:inspection_id>/save", views.inspection_save),
]
