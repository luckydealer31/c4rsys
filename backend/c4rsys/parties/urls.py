from django.urls import path

from c4rsys.parties import link_views, views

urlpatterns = [
    path("c/<str:cmp>/clients", views.client_list),
    path("c/<str:cmp>/clients/matches", views.client_matches),
    path("c/<str:cmp>/clients/<uuid:client_id>", views.client_detail),
    path("c/<str:cmp>/counterparties", views.counterparty_list),
    path("c/<str:cmp>/counterparties/<uuid:counterparty_id>", views.counterparty_detail),
    path("c/<str:cmp>/counterparties/<uuid:counterparty_id>/archive", views.counterparty_archive),
    path("c/<str:cmp>/counterparties/<uuid:counterparty_id>/restore", views.counterparty_restore),
    path("c/<str:cmp>/profile-links", link_views.card_links),
    path("c/<str:cmp>/profile-links/<uuid:link_id>/cancel", link_views.link_cancel),
    path("c/<str:cmp>/profile-links/<uuid:link_id>/break", link_views.link_break),
    path("c/<str:cmp>/profile-links/<uuid:link_id>/apply", link_views.link_apply),
    path("c/<str:cmp>/incoming-links", link_views.incoming),
    path("c/<str:cmp>/incoming-links/<uuid:link_id>/accept", link_views.incoming_accept),
    path("c/<str:cmp>/incoming-links/<uuid:link_id>/decline", link_views.incoming_decline),
    path("profile-links", link_views.my_links),
    path("profile-links/<uuid:link_id>/accept", link_views.my_accept),
    path("profile-links/<uuid:link_id>/decline", link_views.my_decline),
]
