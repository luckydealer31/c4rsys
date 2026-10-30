from django.urls import path

from c4rsys.companies import ownership_views, staff_views, views

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
    # Owners, transfers, representatives, deactivation (§ 3.3–3.6).
    path("c/<str:cmp>/owners", ownership_views.owners),
    path("c/<str:cmp>/owners/transfers", ownership_views.transfer_start),
    path("c/<str:cmp>/owners/seats/<uuid:seat_id>/limit", ownership_views.seat_limit),
    path("c/<str:cmp>/ownership-policy", ownership_views.policy),
    path("c/<str:cmp>/holdings", ownership_views.holdings),
    path(
        "c/<str:cmp>/holdings/<uuid:seat_id>/representative",
        ownership_views.representative_assign,
    ),
    path(
        "c/<str:cmp>/holdings/<uuid:seat_id>/representative/remove",
        ownership_views.representative_remove,
    ),
    path("c/<str:cmp>/holdings/<uuid:seat_id>/transfers", ownership_views.holding_transfer_start),
    path("c/<str:cmp>/state", ownership_views.state),
    path("c/<str:cmp>/deactivation", ownership_views.deactivate),
    path("c/<str:cmp>/restoration", ownership_views.restore),
    path(
        "c/<str:cmp>/state-requests/<uuid:request_id>/cancel",
        ownership_views.state_request_cancel,
    ),
    path(
        "c/<str:cmp>/state-requests/<uuid:request_id>/complete",
        ownership_views.state_request_complete,
    ),
    path("ownership-requests", ownership_views.my_requests),
    path(
        "ownership-requests/transfers/<uuid:approval_id>/approve",
        ownership_views.transfer_approve,
    ),
    path(
        "ownership-requests/transfers/<uuid:approval_id>/decline",
        ownership_views.transfer_decline,
    ),
    path("ownership-requests/states/<uuid:request_id>/approve", ownership_views.state_approve),
    path("ownership-requests/states/<uuid:request_id>/decline", ownership_views.state_decline),
    path("ownership-transfers/<uuid:transfer_id>/cancel", ownership_views.transfer_cancel),
    path("invitations", staff_views.my_invitations),
    path("invitations/<uuid:invitation_id>/accept", staff_views.invitation_accept),
    path("invitations/<uuid:invitation_id>/decline", staff_views.invitation_decline),
]
