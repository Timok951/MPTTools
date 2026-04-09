from django.urls import path

from . import views

urlpatterns = [
    path("", views.analytics, name="analytics"),
    path("equipment/", views.equipment_list, name="equipment_list"),
    path("usage/", views.usage_history, name="usage_history"),
    path("usage/new/", views.usage_create, name="usage_create"),
    path("requests/", views.request_history, name="request_history"),
    path("requests/new/", views.request_create, name="request_create"),
    path("timer/", views.timer_panel, name="timer_panel"),
    path("timer/new/", views.timer_create, name="timer_create"),
    path("search/", views.inventory_search, name="inventory_search"),
    path("workplaces/", views.workplaces, name="workplaces"),
    path("cabinets/", views.cabinets, name="cabinets"),
    path("suppliers/", views.suppliers, name="suppliers"),
    path("adjustments/new/", views.adjustment_create, name="adjustment_create"),
    path("checkouts/", views.checkouts, name="checkouts"),
    path("checkouts/new/", views.checkout_create, name="checkout_create"),
    path("checkouts/<int:checkout_id>/return/", views.checkout_return, name="checkout_return"),
    path("history/", views.history_timeline, name="history"),
    path("reports/", views.reports, name="reports"),
    path("reports/export/<str:report_type>/", views.reports_export, name="reports_export"),
    path("accounts/login/", views.login_view, name="login"),
    path("accounts/logout/", views.logout_view, name="logout"),
    path("accounts/register/", views.register_view, name="register"),
    path("accounts/roles/", views.role_assignment, name="role_assignment"),
]
