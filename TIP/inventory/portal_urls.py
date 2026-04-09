from django.urls import path

from . import portal_views

urlpatterns = [
    path("", portal_views.portal_dashboard, name="portal_home"),
    path("logs/", portal_views.portal_logs, name="portal_logs"),
    path("<slug:entity>/new/", portal_views.portal_create, name="portal_create"),
    path("<slug:entity>/<int:pk>/edit/", portal_views.portal_edit, name="portal_edit"),
    path("<slug:entity>/<int:pk>/delete/", portal_views.portal_delete, name="portal_delete"),
    path("<slug:entity>/<int:pk>/restore/", portal_views.portal_restore, name="portal_restore"),
    path("<slug:entity>/", portal_views.portal_list, name="portal_list"),
]
