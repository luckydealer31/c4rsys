from django.urls import path

from c4rsys.finance import views

urlpatterns = [
    path("c/<str:cmp>/cash-accounts", views.account_list),
    path("c/<str:cmp>/cash-accounts/<uuid:account_id>", views.account_detail),
    path("c/<str:cmp>/cash-accounts/<uuid:account_id>/archive", views.account_archive),
]
