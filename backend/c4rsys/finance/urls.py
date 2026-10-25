from django.urls import path

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
    path("c/<str:cmp>/payments", views.payment_list),
    path("c/<str:cmp>/payments/cash", views.payment_cash),
    path("c/<str:cmp>/clients/<uuid:client_id>/balance", views.client_balance),
]
