from django.urls import path

from c4rsys.documents import views

urlpatterns = [
    path("c/<str:cmp>/document-kinds", views.kind_list),
    path("c/<str:cmp>/documents", views.document_list),
    path("c/<str:cmp>/documents/preview", views.document_preview),
    path("c/<str:cmp>/documents/from-template", views.document_from_template),
    path("c/<str:cmp>/document-fields", views.field_catalog),
    path("c/<str:cmp>/document-templates", views.template_list),
    path("c/<str:cmp>/document-templates/<uuid:template_id>", views.template_detail),
    path("c/<str:cmp>/documents/<uuid:document_id>", views.document_detail),
    path("c/<str:cmp>/documents/<uuid:document_id>/versions", views.document_version),
    path("c/<str:cmp>/documents/<uuid:document_id>/links", views.document_link),
    path("c/<str:cmp>/documents/<uuid:document_id>/unlink", views.document_unlink),
    path("c/<str:cmp>/documents/<uuid:document_id>/archive", views.document_archive),
    path("c/<str:cmp>/documents/<uuid:document_id>/remake", views.document_remake),
    path("c/<str:cmp>/document-kinds/signing", views.kind_signing),
    path("c/<str:cmp>/documents/<uuid:document_id>/signing", views.document_signing),
    path(
        "c/<str:cmp>/documents/<uuid:document_id>/signers/<uuid:signer_id>/code", views.signer_code
    ),
    path(
        "c/<str:cmp>/documents/<uuid:document_id>/signers/<uuid:signer_id>/decide",
        views.signer_decide,
    ),
    path(
        "c/<str:cmp>/documents/<uuid:document_id>/signers/<uuid:signer_id>/invite",
        views.signer_invite,
    ),
    path(
        "c/<str:cmp>/documents/<uuid:document_id>/signers/<uuid:signer_id>/revoke",
        views.signer_revoke,
    ),
    path(
        "c/<str:cmp>/documents/<uuid:document_id>/signers/<uuid:signer_id>/paper",
        views.signer_paper,
    ),
    path("sign/<str:token>", views.link_open),
    path("sign/<str:token>/file", views.link_file),
    path("sign/<str:token>/code", views.link_code),
    path("sign/<str:token>/decide", views.link_decide),
]
