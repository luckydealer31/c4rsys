from django.urls import path

from c4rsys.tasks import views

urlpatterns = [
    path("c/<str:cmp>/tasks", views.task_list),
    path("c/<str:cmp>/tasks/assignees", views.assignees),
    path("c/<str:cmp>/tasks/policy", views.policy),
    path("c/<str:cmp>/tasks/groups", views.group_list),
    path("c/<str:cmp>/tasks/groups/<uuid:group_id>", views.group_detail),
    path("c/<str:cmp>/tasks/tags", views.tag_list),
    path("c/<str:cmp>/tasks/tags/<uuid:tag_id>", views.tag_detail),
    path("c/<str:cmp>/tasks/<uuid:task_id>", views.task_detail),
    path("c/<str:cmp>/tasks/<uuid:task_id>/repeat", views.task_repeat),
    path("c/<str:cmp>/tasks/<uuid:task_id>/<str:action>", views.task_action),
]
