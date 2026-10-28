from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    # factures
    path("factures/", views.invoice_list, name="invoice_list"),
    path("factures/nouvelle/", views.invoice_editor, name="invoice_create"),
    path("factures/prochain-numero/", views.next_number, name="next_number"),
    path("factures/synchroniser/", views.invoice_sync, name="invoice_sync"),
    path("factures/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("factures/<int:pk>/modifier/", views.invoice_edit, name="invoice_edit"),
    path("factures/<int:pk>/brouillon/", views.invoice_editor, name="invoice_draft_edit"),
    path("factures/<int:pk>/supprimer/", views.invoice_delete, name="invoice_delete"),
    path("factures/<int:pk>/paiement/", views.invoice_payment, name="invoice_payment"),
    path("factures/<int:pk>/imprimer/", views.invoice_print, name="invoice_print"),
    path("factures/<int:pk>/actualiser/", views.invoice_refresh, name="invoice_refresh"),
    path("factures/<int:pk>/confirmer/", views.invoice_confirm, name="invoice_confirm"),
    path("factures/<int:pk>/annuler/", views.invoice_cancel, name="invoice_cancel"),
    # clients
    path("clients/", views.client_list, name="client_list"),
    path("clients/nouveau/", views.client_edit, name="client_create"),
    path("clients/<int:pk>/modifier/", views.client_edit, name="client_edit"),
    path("clients/<int:pk>/supprimer/", views.client_delete, name="client_delete"),
    # sociétés
    path("societes/", views.company_list, name="company_list"),
    path("societes/nouvelle/", views.company_edit, name="company_create"),
    path("societes/<int:pk>/modifier/", views.company_edit, name="company_edit"),
    # API
    path("api/infos/", views.api_info, name="api_info"),
]
