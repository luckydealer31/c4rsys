from django.urls import path

from c4rsys.backoffice import company_views, views

S = "staff/"
P = "staff/privileged/<uuid:session_id>/"

urlpatterns = [
    path(f"{S}me", views.me),
    path(f"{S}users", views.user_search),
    path(f"{S}users/<str:usr>", views.user_detail),
    path(f"{S}users/<str:usr>/contacts", views.user_reveal),
    path(f"{S}companies", views.company_search),
    path(f"{S}companies/<str:cmp>", views.company_detail),
    path(f"{S}companies/<str:cmp>/contacts", views.company_reveal),
    path(f"{S}tickets", views.ticket_list),
    path(f"{S}tickets/<uuid:ticket_id>", views.ticket_detail),
    path(f"{S}tickets/<uuid:ticket_id>/state", views.ticket_state),
    path(f"{S}tickets/<uuid:ticket_id>/assign", views.ticket_assign),
    path(f"{S}tickets/<uuid:ticket_id>/comments", views.ticket_comment),
    path(f"{S}tickets/<uuid:ticket_id>/links", views.ticket_link),
    path(f"{S}members", views.member_list),
    path(f"{S}members/<uuid:member_id>", views.member_change),
    path(f"{S}members/<uuid:member_id>/revoke", views.member_revoke),
    path(f"{S}roles", views.role_list),
    path(f"{S}journal", views.journal),
    path(f"{S}journal/export", views.journal_export),
    path(f"{S}privileged", views.session_list),
    path(f"{S}privileged/<uuid:session_id>", views.session_detail),
    path(f"{P}close", views.session_close),
    path(f"{P}notice/defer", views.session_defer),
    path(f"{P}notice/release", views.session_release),
    path(f"{P}company", views.pv_company),
    path(f"{P}employees", views.pv_employees),
    path(f"{P}vehicles", views.pv_vehicles),
    path(f"{P}clients", views.pv_clients),
    path(f"{P}orders", views.pv_orders),
    path(f"{P}orders/<uuid:order_id>", views.pv_order),
    path(f"{P}rentals", views.pv_rentals),
    path(f"{P}rentals/<uuid:rental_id>", views.pv_rental),
    path(f"{P}documents", views.pv_documents),
    path(f"{P}documents/<uuid:document_id>/file", views.pv_document_file),
    path(f"{P}finance", views.pv_finance),
    path(f"{P}user", views.pv_user),
    path(f"{S}blocks", views.block_list),
    path(f"{S}blocks/<uuid:block_id>/restore", views.block_restore),
    path(f"{S}blocks/<uuid:block_id>/notice/release", views.block_release),
    # The company side (§ 24.3.2), on the ordinary domain.
    path("c/<str:cmp>/staff-access", company_views.staff_access),
]
