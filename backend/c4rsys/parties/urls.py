from django.urls import path

from c4rsys.parties import views

urlpatterns = [
    path("c/<str:cmp>/clients", views.client_list),
    path("c/<str:cmp>/clients/matches", views.client_matches),
    path("c/<str:cmp>/clients/<uuid:client_id>", views.client_detail),
    path("c/<str:cmp>/counterparties", views.counterparty_list),
    path("c/<str:cmp>/counterparties/<uuid:counterparty_id>", views.counterparty_detail),
    path("c/<str:cmp>/counterparties/<uuid:counterparty_id>/archive", views.counterparty_archive),
    path("c/<str:cmp>/counterparties/<uuid:counterparty_id>/restore", views.counterparty_restore),
]
