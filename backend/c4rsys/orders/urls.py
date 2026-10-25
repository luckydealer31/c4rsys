from django.urls import path

from c4rsys.orders import views

urlpatterns = [
    path("c/<str:cmp>/orders", views.order_list),
    path("c/<str:cmp>/orders/<uuid:order_id>", views.order_detail),
    path("c/<str:cmp>/orders/<uuid:order_id>/close", views.order_close),
    path("c/<str:cmp>/orders/<uuid:order_id>/reopen", views.order_reopen),
    path("c/<str:cmp>/orders/<uuid:order_id>/history", views.order_history),
]
