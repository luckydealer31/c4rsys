from django.urls import path

from c4rsys.finance import deposit_views as dv
from c4rsys.finance import settlement_views as sv
from c4rsys.finance import views

urlpatterns = [
    path("c/<str:cmp>/cash-accounts", views.account_list),
    path("c/<str:cmp>/cash-accounts/<uuid:account_id>", views.account_detail),
    path("c/<str:cmp>/cash-accounts/<uuid:account_id>/archive", views.account_archive),
    path("c/<str:cmp>/cash-accounts/<uuid:account_id>/records", views.account_records),
    path("c/<str:cmp>/cash-accounts/<uuid:account_id>/opening", views.account_opening),
    path("c/<str:cmp>/money-records", views.money_record),
    path("c/<str:cmp>/finance-articles", views.article_list),
    path("c/<str:cmp>/finance-articles/<uuid:article_id>", views.article_detail),
    path("c/<str:cmp>/finance-articles/<uuid:article_id>/archive", views.article_archive),
    path("c/<str:cmp>/finance-articles/<uuid:article_id>/restore", views.article_restore),
    path("c/<str:cmp>/exchange-rates/suggestion", views.rate_suggestion),
    path("c/<str:cmp>/transfers", views.transfer_create),
    path("c/<str:cmp>/settlements", sv.settlement_lists),
    path("c/<str:cmp>/clients/<uuid:client_id>/settlement", sv.client_settlement),
    path(
        "c/<str:cmp>/counterparties/<uuid:counterparty_id>/settlement", sv.counterparty_settlement
    ),
    path("c/<str:cmp>/supplier-statement", sv.supplier_statement),
    path("c/<str:cmp>/settlement-entries", sv.opening_create),
    path("c/<str:cmp>/settlement-entries/<uuid:entry_id>/cancel", sv.entry_cancel),
    path("c/<str:cmp>/supplier-returns/<uuid:operation_id>/settle", sv.return_settle),
    path("c/<str:cmp>/supplier-payments", sv.supplier_payment),
    path("c/<str:cmp>/refunds", sv.client_refund),
    path("c/<str:cmp>/offsets", sv.offset_create),
    path("c/<str:cmp>/offsets/<uuid:offset_id>/cancel", sv.offset_cancel),
    path("c/<str:cmp>/offsets/<uuid:offset_id>/correct", sv.offset_correct),
    path("c/<str:cmp>/money-records/<uuid:movement_id>/reverse", sv.record_reverse),
    path("c/<str:cmp>/money-records/<uuid:movement_id>/shares", sv.record_shares),
    path("c/<str:cmp>/payment-allocations/<uuid:allocation_id>/relink", sv.allocation_relink),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/deposit", dv.deposit_detail),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/deposit/to-balance", dv.deposit_to_balance),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/deposit/hold", dv.deposit_hold),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/deposit/release", dv.deposit_release),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/deposit/offset", dv.deposit_offset),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/deposit/refund", dv.deposit_refund),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/deposit/waive", dv.deposit_waive),
    path("c/<str:cmp>/payments", views.payment_list),
    path("c/<str:cmp>/payments/cash", views.payment_cash),
    path("c/<str:cmp>/clients/<uuid:client_id>/balance", views.client_balance),
]
