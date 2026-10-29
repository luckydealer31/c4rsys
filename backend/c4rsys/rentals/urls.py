from django.urls import path

from c4rsys.rentals import booking_views as bv
from c4rsys.rentals import change_views as chv
from c4rsys.rentals import charge_views as cv
from c4rsys.rentals import extra_views as ev
from c4rsys.rentals import order_views as ov
from c4rsys.rentals import views

urlpatterns = [
    path("c/<str:cmp>/extra-services", ev.service_list),
    path("c/<str:cmp>/extra-services/applicable", ev.applicable),
    path("c/<str:cmp>/extra-services/<uuid:service_id>", ev.service_detail),
    path("c/<str:cmp>/extra-services/<uuid:service_id>/archive", ev.service_archive),
    path("c/<str:cmp>/extra-services/<uuid:service_id>/restore", ev.service_restore),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/services", ev.rental_service_add),
    path("c/<str:cmp>/orders/<uuid:order_id>/services", ev.order_services),
    path("c/<str:cmp>/service-lines", ev.line_create),
    path("c/<str:cmp>/service-lines/<uuid:line_id>", ev.line_detail),
    path("c/<str:cmp>/service-lines/<uuid:line_id>/start", ev.line_start),
    path("c/<str:cmp>/service-lines/<uuid:line_id>/complete", ev.line_complete),
    path("c/<str:cmp>/service-lines/<uuid:line_id>/cancel", ev.line_cancel),
    path("c/<str:cmp>/service-lines/<uuid:line_id>/unlink", ev.line_unlink),
    path("c/<str:cmp>/service-lines/<uuid:line_id>/free-time", ev.line_free_add),
    path(
        "c/<str:cmp>/service-lines/<uuid:line_id>/free-time/<uuid:interval_id>/remove",
        ev.line_free_remove,
    ),
    path("c/<str:cmp>/readiness", views.company_readiness),
    path("c/<str:cmp>/setup/complete", views.company_setup_complete),
    path("c/<str:cmp>/setup/choice", views.company_setup_choice),
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
    path("c/<str:cmp>/rentals/<uuid:rental_id>/planned-end", chv.planned_end),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/tariff", chv.tariff),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/reopen", chv.reopen),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/correct", chv.correct),
    # § 12.1.7, § 12.3.7 (decisions З2, З5, З6): proposals, annulment, transfer.
    path("c/<str:cmp>/orders/<uuid:order_id>/rental-proposals", ov.proposals),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/withdraw-proposal", ov.withdraw),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/annul", ov.annul),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/transfer", ov.transfer),
    path("c/<str:cmp>/inspections/<uuid:inspection_id>/save", views.inspection_save),
]
