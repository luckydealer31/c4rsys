from django.urls import path

from c4rsys.rentals import booking_views as bv
from c4rsys.rentals import charge_views as cv
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
    path("c/<str:cmp>/bookings", bv.booking_create),
    path("c/<str:cmp>/bookings/availability", bv.availability),
    path("c/<str:cmp>/bookings/<uuid:rental_id>", bv.booking_update),
    path("c/<str:cmp>/bookings/<uuid:rental_id>/vehicle", bv.booking_vehicle),
    path("c/<str:cmp>/bookings/<uuid:rental_id>/cancel", bv.booking_cancel),
    path("c/<str:cmp>/bookings/<uuid:rental_id>/auto-cancel", bv.booking_auto_cancel),
    path("c/<str:cmp>/bookings/<uuid:rental_id>/deposit", bv.booking_deposit),
    path("c/<str:cmp>/rental-policy", bv.policy),
    path("c/<str:cmp>/issue-policy", bv.issue_policy),
    path("c/<str:cmp>/bookings/<uuid:rental_id>/issue-exception", bv.booking_exception),
    path(
        "c/<str:cmp>/bookings/<uuid:rental_id>/issue-exception/revoke",
        bv.booking_exception_revoke,
    ),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/issue-exception/decide", bv.exception_decide),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/free-time", cv.free_time_add),
    path(
        "c/<str:cmp>/rentals/<uuid:rental_id>/free-time/<uuid:interval_id>",
        cv.free_time_change,
    ),
    path(
        "c/<str:cmp>/rentals/<uuid:rental_id>/free-time/<uuid:interval_id>/remove",
        cv.free_time_remove,
    ),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/manual-charges", cv.manual_charge_add),
    path(
        "c/<str:cmp>/rentals/<uuid:rental_id>/manual-charges/<uuid:charge_id>/cancel",
        cv.manual_charge_cancel,
    ),
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
