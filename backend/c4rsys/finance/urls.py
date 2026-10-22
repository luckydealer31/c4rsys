from django.urls import path

from c4rsys.finance import views

urlpatterns = [
    path("c/<str:cmp>/cash-accounts", views.account_list),
    path("c/<str:cmp>/cash-accounts/<uuid:account_id>", views.account_detail),
    path("c/<str:cmp>/cash-accounts/<uuid:account_id>/archive", views.account_archive),
    path("c/<str:cmp>/payments", views.payment_list),
    path("c/<str:cmp>/payments/cash", views.payment_cash),
    path("c/<str:cmp>/clients/<uuid:client_id>/balance", views.client_balance),
]
