from django.urls import path

from c4rsys.tasks import views

urlpatterns = [
    path("c/<str:cmp>/tasks", views.task_list),
    path("c/<str:cmp>/tasks/assignees", views.assignees),
    path("c/<str:cmp>/tasks/policy", views.policy),
    path("c/<str:cmp>/tasks/<uuid:task_id>", views.task_detail),
    path("c/<str:cmp>/tasks/<uuid:task_id>/<str:action>", views.task_action),
]
