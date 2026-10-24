from django.urls import path

from c4rsys.companies import staff_views, views

urlpatterns = [
    path("companies", views.companies),
    path("c/<str:cmp>", views.company),
    path("c/<str:cmp>/locations", views.location_list),
    path("c/<str:cmp>/locations/<uuid:location_id>", views.location_detail),
    path("c/<str:cmp>/locations/<uuid:location_id>/contexts", views.location_contexts),
    path("c/<str:cmp>/locations/<uuid:location_id>/deactivate", views.location_deactivate),
    path("c/<str:cmp>/locations/<uuid:location_id>/restore", views.location_restore),
    path(
        "c/<str:cmp>/locations/<uuid:location_id>/contexts/<uuid:context_id>/disable",
        views.context_disable,
    ),
    path(
        "c/<str:cmp>/locations/<uuid:location_id>/contexts/<uuid:context_id>/enable",
        views.context_enable,
    ),
    path(
        "c/<str:cmp>/locations/<uuid:location_id>/contexts/<uuid:context_id>/storage",
        views.context_storage,
    ),
    path("c/<str:cmp>/employees", staff_views.employee_list),
    path("c/<str:cmp>/permission-catalog", staff_views.permission_catalog),
    path("c/<str:cmp>/employees/invitations", staff_views.invitation_create),
    path(
        "c/<str:cmp>/employees/invitations/<uuid:invitation_id>/cancel",
        staff_views.invitation_cancel,
    ),
    path("c/<str:cmp>/employees/<uuid:employment_id>", staff_views.employee_detail),
    path("c/<str:cmp>/employees/<uuid:employment_id>/rights", staff_views.employee_rights),
    path("c/<str:cmp>/employees/<uuid:employment_id>/journal", staff_views.employee_journal),
    path("c/<str:cmp>/employees/<uuid:employment_id>/block", staff_views.employee_block),
    path("c/<str:cmp>/employees/<uuid:employment_id>/restore", staff_views.employee_restore),
    path("invitations", staff_views.my_invitations),
    path("invitations/<uuid:invitation_id>/accept", staff_views.invitation_accept),
    path("invitations/<uuid:invitation_id>/decline", staff_views.invitation_decline),
]
