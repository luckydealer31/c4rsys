from django.urls import path

from c4rsys.orders import approval_views, discount_views, views

urlpatterns = [
    path("c/<str:cmp>/orders", views.order_list),
    path("c/<str:cmp>/orders/<uuid:order_id>", views.order_detail),
    path("c/<str:cmp>/orders/<uuid:order_id>/close", views.order_close),
    path("c/<str:cmp>/orders/<uuid:order_id>/reopen", views.order_reopen),
    path("c/<str:cmp>/orders/<uuid:order_id>/form", views.order_form),
    path("c/<str:cmp>/orders/<uuid:order_id>/responsible", views.order_responsible),
    path("c/<str:cmp>/orders/<uuid:order_id>/cancel", views.order_cancel),
    path("c/<str:cmp>/orders/<uuid:order_id>/history", views.order_history),
    path("c/<str:cmp>/order-policy", approval_views.policy),
    path("c/<str:cmp>/discounts", discount_views.catalog),
    path("c/<str:cmp>/discounts/<uuid:discount_id>/archive", discount_views.catalog_archive),
    path("c/<str:cmp>/orders/<uuid:order_id>/discounts", discount_views.order_discounts),
    path(
        "c/<str:cmp>/orders/<uuid:order_id>/discounts/<uuid:applied_id>/remove",
        discount_views.order_discount_remove,
    ),
    path(
        "c/<str:cmp>/employees/<uuid:employment_id>/discount-limit",
        discount_views.discount_limit,
    ),
    path("c/<str:cmp>/orders/<uuid:order_id>/approvals", approval_views.order_approvals),
    path(
        "c/<str:cmp>/orders/<uuid:order_id>/approvals/<uuid:approval_id>/proceed",
        approval_views.approval_proceed,
    ),
    path(
        "c/<str:cmp>/orders/<uuid:order_id>/approvals/<uuid:approval_id>/answer",
        approval_views.approval_answer,
    ),
]
