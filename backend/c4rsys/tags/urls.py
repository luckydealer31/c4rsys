from django.urls import path

from c4rsys.tags import views

urlpatterns = [
    path("c/<str:cmp>/tags", views.tag_list),
    path("c/<str:cmp>/tags/<uuid:tag_id>", views.tag_detail),
    path("c/<str:cmp>/tags/<uuid:tag_id>/objects", views.tag_objects),
    path("c/<str:cmp>/tags/<uuid:tag_id>/<str:action>", views.tag_action),
    path("c/<str:cmp>/tag-links", views.tag_links),
    path("c/<str:cmp>/tag-groups", views.group_list),
    path("c/<str:cmp>/tag-groups/<uuid:group_id>", views.group_detail),
]
