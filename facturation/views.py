import base64
import io
import json
from decimal import ROUND_HALF_UP, Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import calculations as calc
from . import conf, documents
from .emcf_client import EmcfClient, EmcfError, build_payload
from .forms import (
    ClientForm, CompanyForm, InvoiceForm, InvoiceInfoForm, ItemFormSet, PaymentFormSet,
)
from .models import (
    PAYMENT_METHODS, TAX_GROUPS, Client, CompanySettings, Invoice, InvoiceItem, Payment,
    next_internal_number,
)


def _pretty(data):
    return json.dumps(data, indent=2, ensure_ascii=False)


def _qr_data_uri(text):
    """QR code en image base64 (nécessite le paquet 'qrcode' + Pillow)."""
    if not text:
        return None
    try:
        import qrcode
    except ImportError:
        return None
    buffer = io.BytesIO()
    qrcode.make(text, box_size=6, border=2).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _safe_next(request):
    """URL de retour ('next') acceptée uniquement si elle pointe vers ce site."""
    url = request.POST.get("next") or request.GET.get("next") or ""
    if url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return url
    return ""


# --------------------------------------------------------------------------- tableau de bord
@login_required
def dashboard(request):
    companies = CompanySettings.objects.annotate(nb_invoices=Count("invoices"))
    counts = {s: Invoice.objects.filter(status=s).count() for s, _ in Invoice.Status.choices}
    return render(request, "facturation/dashboard.html", {
        "companies": companies,
        "counts": counts,
        "nb_clients": Client.objects.count(),
        "recent": Invoice.objects.select_related("company")[:8],
    })


# --------------------------------------------------------------------------- liste des factures
@login_required
def invoice_list(request):
    qs = Invoice.objects.select_related("company", "client")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    inv_type = request.GET.get("type", "")
    company_id = request.GET.get("company", "")
    pay = request.GET.get("pay", "")
    if q:
        qs = qs.filter(
            Q(client_name__icontains=q) | Q(uid__icontains=q) | Q(client_ifu__icontains=q)
            | Q(internal_number__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    if inv_type:
        qs = qs.filter(type=inv_type)
    if company_id.isdigit():
        qs = qs.filter(company_id=company_id)
    if pay:
        qs = qs.filter(payment_status=pay)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "facturation/invoice_list.html", {
        "page": page, "q": q, "status": status, "inv_type": inv_type, "company_id": company_id,
        "pay": pay, "payment_statuses": Invoice.PaymentStatus.choices,
        "statuses": Invoice.Status.choices, "types": Invoice.TYPES,
        "companies": CompanySettings.objects.all(),
    })


# --------------------------------------------------------------------------- création / édition de facture
def _confirm_invoice(invoice):
    if not invoice.uid:
        raise EmcfError("Cette facture n'a pas encore été envoyée à la DGI (pas d'UID).")
    data = EmcfClient(invoice.issuer).confirm(invoice.uid)
    invoice.security = data
    invoice.status = Invoice.Status.CONFIRMED
    invoice.confirmed_at = timezone.now()
    invoice.save()


def _unit_ht(item):
    """Prix HT unitaire d'une ligne ; pour l'historique (TTC seul) on le déduit du TTC."""
    if item.price_ht is not None:
        return item.price_ht
    rate = conf.tax_rates().get(item.tax_group, 0)
    return calc.compute_line(item.quantity or 1, rate, unit_ttc=item.price)["unit_ht"]


def _initial_from_invoice(invoice, credit_note=False):
    """Valeurs initiales du formulaire, des lignes et des paiements à partir d'une facture."""
    form_initial = {
        "company": invoice.company_id, "client": invoice.client_id,
        "shipping_address": invoice.shipping_address, "aib": invoice.aib,
        "type": invoice.type, "reference": invoice.reference,
        "invoice_date": invoice.invoice_date, "due_date": invoice.due_date,
        "payment_status": invoice.payment_status, "internal_number": invoice.internal_number,
        "payment_communication": invoice.payment_communication, "note": invoice.note,
        "amount_remaining": invoice.amount_remaining,
    }
    items_initial = []
    for it in invoice.items.all():
        if it.kind == InvoiceItem.Kind.ITEM:
            items_initial.append({"kind": "item", "name": it.name, "quantity": it.quantity,
                                  "unit_ht": _unit_ht(it), "tax_group": it.tax_group})
        else:
            items_initial.append({"kind": it.kind, "name": it.name})
    pays_initial = [{"method": p.method, "amount": p.amount} for p in invoice.payments.all()]
    if credit_note:  # avoir pré-rempli depuis une facture
        issuer = invoice.issuer
        form_initial = {
            "company": issuer.pk if issuer else None, "client": invoice.client_id,
            "shipping_address": invoice.shipping_address, "aib": invoice.aib,
            "type": "EA" if invoice.type in ("EV", "EA") else "FA", "reference": invoice.uid or "",
        }
        pays_initial = []
        if issuer:
            form_initial["internal_number"] = next_internal_number(issuer, form_initial["type"])
            form_initial["payment_communication"] = form_initial["internal_number"]
    return form_initial, items_initial, pays_initial


def _editor_response(request, form, items, pays, invoice=None):
    return render(request, "facturation/invoice_form.html", {
        "form": form, "items": items, "pays": pays, "invoice": invoice,
        "tax_rates": {k: float(v) for k, v in conf.tax_rates().items()},
        "tax_labels": dict(TAX_GROUPS),
        "unverified_groups": ["C", "D", "E", "F"],
        "aib_rates": {code: d["rate"] for code, d in conf.aib_definitions().items()},
        "client_shipping": {str(c.pk): c.shipping_address for c in Client.objects.all()},
    })


def _rows_from_formset(items):
    """Lignes valides, dans l'ordre choisi par l'utilisateur (champ ORDER), sans les lignes supprimées."""
    rows = []
    for form in items.ordered_forms:
        d = form.cleaned_data
        row = {"kind": d["kind"], "name": d["name"]}
        if d["kind"] == "item":
            row.update(quantity=d["quantity"], unit_ht=d["unit_ht"], tax_group=d["tax_group"])
        rows.append(row)
    return rows


def _save_invoice(invoice, d, result, pay_rows, user):
    """Enregistre (ou met à jour) un brouillon avec ses lignes, ses paiements et ses montants."""
    company, client = d["company"], d["client"]
    number = d["internal_number"].strip() or next_internal_number(company, d["type"])
    with transaction.atomic():
        if invoice is None:
            invoice = Invoice(created_by=user, status=Invoice.Status.DRAFT)
        invoice.company, invoice.client = company, client
        invoice.type, invoice.reference, invoice.aib = d["type"], d["reference"], d["aib"]
        invoice.internal_number = number
        invoice.payment_communication = d["payment_communication"].strip() or number
        invoice.note = d["note"]
        invoice.invoice_date, invoice.due_date = d["invoice_date"], d["due_date"]
        invoice.payment_status = d["payment_status"]
        invoice.shipping_address = d["shipping_address"]
        invoice.amount_remaining = d["amount_remaining"]
        # Le client n'avait pas d'adresse d'expédition : celle saisie ici est mémorisée dans sa fiche.
        if client and d["shipping_address"].strip() and not client.shipping_address.strip():
            client.shipping_address = d["shipping_address"].strip()
            client.save(update_fields=["shipping_address"])
        invoice.client_name = client.name if client else ""
        invoice.client_ifu = client.ifu if client else ""
        invoice.client_contact = client.contact if client else ""
        invoice.client_address = client.address if client else ""
        invoice.total_ht, invoice.total_tax = result["total_ht"], result["total_tax"]
        invoice.aib_amount, invoice.total = result["aib_amount"], result["total_ttc"]
        invoice.save()
        invoice.items.all().delete()
        position = 0
        for row in result["rows"]:
            if row["kind"] == "subtotal":
                continue
            InvoiceItem.objects.create(
                invoice=invoice, kind=row["kind"], position=position, name=row["name"],
                price=row.get("unit_ttc", 0), price_ht=row.get("unit_ht"),
                quantity=row.get("quantity", 0), tax_group=row.get("tax_group", "B"),
            )
            position += 1
        invoice.payments.all().delete()
        for p in pay_rows:
            Payment.objects.create(invoice=invoice, method=p["method"], amount=p["amount"])
    return invoice


def _submit_and_confirm(request, invoice):
    """Envoie le brouillon à la DGI puis le confirme. Retourne True si la facture est confirmée.

    * un verrou (brouillon -> en attente, en une seule requête SQL) empêche la double confirmation ;
    * le total calculé ici est comparé à celui de la DGI : en cas d'écart, la facture reste « En attente »
      et n'est PAS confirmée (les règles fiscales locales ne sont donc jamais imposées à la DGI).
    """
    company = invoice.company
    result = invoice.compute()
    claimed = Invoice.objects.filter(pk=invoice.pk, status=Invoice.Status.DRAFT).update(
        status=Invoice.Status.PENDING
    )
    if not claimed:
        messages.warning(request, "Cette facture a déjà été envoyée ou confirmée : aucune action effectuée.")
        return False

    def release():
        Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.DRAFT)

    data = {"type": invoice.type, "client_name": invoice.client_name, "client_ifu": invoice.client_ifu,
            "client_contact": invoice.client_contact, "client_address": invoice.client_address,
            "aib": "",  # l'AIB est une information client : jamais transmis à l'API e-MCF
            "reference": invoice.reference}
    lines = [{"name": r["name"], "price": r["unit_ttc"], "quantity": r["quantity"],
              "tax_group": r["tax_group"]} for r in result["rows"] if r["kind"] == "item"]
    payments = [{"method": p.method, "amount": p.amount} for p in invoice.payments.all()]
    payload = build_payload(company.ifu, company.operator_name, data, lines, payments)

    try:
        resp = EmcfClient(company).create(payload)
    except EmcfError as exc:
        release()
        messages.error(request, f"Facture rejetée par l'API e-MCF (elle reste en brouillon) : {exc}")
        return False
    uid = resp.get("uid") if isinstance(resp, dict) else None
    if not uid:
        release()
        messages.error(request, f"Réponse inattendue de l'API (pas d'uid) : {resp}")
        return False

    invoice.refresh_from_db()
    invoice.uid, invoice.request_payload, invoice.create_response = uid, payload, resp
    invoice.save(update_fields=["uid", "request_payload", "create_response"])

    api_total = resp.get("total")
    if api_total is not None:
        try:
            gap = abs(Decimal(str(api_total)) - invoice.total)
        except Exception:
            gap = None
        if gap is not None and gap > conf.TOTAL_TOLERANCE:
            messages.warning(
                request,
                f"Écart de total : {invoice.total} calculé ici, {api_total} calculé par la DGI. "
                "La facture est restée EN ATTENTE et n'a pas été confirmée. Vérifiez les règles du groupe de "
                "taxe / de l'AIB utilisés, puis annulez-la (puis refaites-la) ou confirmez-la si l'écart est attendu.",
            )
            return False
    try:
        _confirm_invoice(invoice)
    except EmcfError as exc:
        messages.warning(request, f"Facture envoyée mais confirmation impossible (statut « En attente ») : {exc}")
        return False
    messages.success(request, "Facture confirmée : éléments de sécurité DGI enregistrés.")
    return True


@login_required
def invoice_editor(request, pk=None):
    """Création d'une facture ou édition d'un brouillon : brouillon, prévisualisation ou confirmation."""
    invoice = None
    if pk:
        invoice = get_object_or_404(Invoice, pk=pk)
        if not invoice.is_draft:
            messages.warning(
                request,
                "Seul un brouillon peut être modifié. Pour une facture envoyée : « Modifier n° / note », "
                "ou émettez un avoir.",
            )
            return redirect("invoice_detail", pk=pk)
    if not CompanySettings.objects.exists():
        messages.warning(request, "Ajoutez d'abord la société qui émettra les factures.")
        return redirect("company_create")

    if request.method != "POST":
        source = get_object_or_404(Invoice, pk=request.GET["from"]) if request.GET.get("from") else None
        if invoice or source:
            form_initial, items_initial, pays_initial = _initial_from_invoice(invoice or source, credit_note=bool(source))
        else:
            form_initial, items_initial, pays_initial = {}, [{"kind": "item", "quantity": 1, "tax_group": "B"}], []
            if request.GET.get("company"):
                form_initial["company"] = request.GET["company"]
            elif CompanySettings.objects.count() == 1:
                form_initial["company"] = CompanySettings.objects.first().pk
            if request.GET.get("client"):
                form_initial["client"] = request.GET["client"]
                chosen_client = Client.objects.filter(pk=request.GET["client"]).first()
                if chosen_client:
                    form_initial["shipping_address"] = chosen_client.shipping_address
            chosen = CompanySettings.objects.filter(pk=form_initial.get("company") or 0).first()
            if chosen:
                form_initial["internal_number"] = next_internal_number(chosen, "FV")
                form_initial["payment_communication"] = form_initial["internal_number"]
        return _editor_response(
            request, InvoiceForm(initial=form_initial, invoice_pk=pk),
            ItemFormSet(prefix="items", initial=items_initial),
            PaymentFormSet(prefix="pays", initial=pays_initial), invoice,
        )

    action = request.POST.get("action", "draft")
    form = InvoiceForm(request.POST, invoice_pk=pk)
    items = ItemFormSet(request.POST, prefix="items")
    pays = PaymentFormSet(request.POST, prefix="pays")
    if not all([form.is_valid(), items.is_valid(), pays.is_valid()]):  # tous évalués : toutes les erreurs s'affichent
        messages.error(request, "Certains champs sont invalides : corrigez-les puis recommencez.")
        return _editor_response(request, form, items, pays, invoice)

    d = form.cleaned_data
    company, client = d["company"], d["client"]
    rows = _rows_from_formset(items)
    result = calc.compute_document(
        rows, conf.tax_rates(), aib_rate=conf.aib_rate(d["aib"]), tax_labels=dict(TAX_GROUPS),
    )

    if action == "preview":  # proforma : rien n'est enregistré, aucun statut n'est modifié
        return render(request, "facturation/invoice_print.html",
                      {"doc": documents.doc_from_form(company, client, d, result), "qr": None})

    problems = []
    if action == "confirm":
        if result["item_count"] < 1:
            problems.append("Ajoutez au moins une ligne d'article avant de confirmer.")
        if result["total_ttc"] <= 0:
            problems.append("Le total de la facture doit être supérieur à 0.")
        if not company.ifu:
            problems.append(f"Renseignez d'abord l'IFU de la société « {company.name} ».")

    pay_rows = [
        {"method": f.cleaned_data["method"], "amount": f.cleaned_data["amount"]}
        for f in pays.forms
        if f.cleaned_data and not f.cleaned_data.get("DELETE") and f.cleaned_data.get("amount")
    ]
    if pay_rows and action == "confirm":
        paid = sum((p["amount"] for p in pay_rows), Decimal(0))
        if paid != result["total_ttc"]:
            problems.append(
                f"La somme des paiements ({paid}) doit être égale au total TTC de la facture ({result['total_ttc']})."
            )
    elif not pay_rows and action == "confirm":
        pay_rows = [{"method": "ESPECES", "amount": result["total_ttc"]}]
    if problems:
        for text in problems:
            messages.error(request, text)
        return _editor_response(request, form, items, pays, invoice)

    invoice = _save_invoice(invoice, d, result, pay_rows, request.user)
    if action == "confirm":
        _submit_and_confirm(request, invoice)
    else:
        messages.success(request, "Brouillon enregistré : vous pouvez le rouvrir et le compléter.")
    return redirect("invoice_detail", pk=invoice.pk)


# --------------------------------------------------------------------------- détail & actions
@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("company", "client"), pk=pk)
    return render(request, "facturation/invoice_detail.html", {
        "invoice": invoice,
        "doc": documents.doc_from_invoice(invoice),
        "payment_statuses": Invoice.PaymentStatus.choices,
        "payload_json": _pretty(invoice.request_payload),
        "details_json": _pretty(invoice.api_details) if invoice.api_details else "",
        "security_json": _pretty(invoice.security) if invoice.security else "",
    })


@login_required
@require_POST
def invoice_refresh(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if not invoice.uid:
        messages.warning(request, "Ce brouillon n'a pas été envoyé à la DGI : rien à actualiser.")
        return redirect("invoice_detail", pk=pk)
    try:
        invoice.api_details = EmcfClient(invoice.issuer).details(invoice.uid)
        invoice.save(update_fields=["api_details"])
        messages.success(request, "Détails récupérés depuis l'API e-MCF.")
    except EmcfError as exc:
        messages.error(request, f"Erreur API : {exc}")
    return redirect("invoice_detail", pk=pk)


@login_required
@require_POST
def invoice_confirm(request, pk):
    """Confirme une facture EN ATTENTE (déjà envoyée à la DGI). Un brouillon se confirme dans l'éditeur."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status != Invoice.Status.PENDING:
        messages.warning(request, "Seule une facture en attente peut être confirmée ici.")
    else:
        try:
            _confirm_invoice(invoice)
            messages.success(request, "Facture confirmée : éléments de sécurité enregistrés.")
        except EmcfError as exc:
            messages.error(request, f"Confirmation refusée : {exc}")
    return redirect("invoice_detail", pk=pk)


@login_required
@require_POST
def invoice_cancel(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status != Invoice.Status.PENDING or not invoice.uid:
        messages.warning(
            request,
            "Seule une facture en attente peut être annulée. Pour corriger une facture "
            "confirmée, émettez une facture d'avoir.",
        )
    else:
        try:
            EmcfClient(invoice.issuer).cancel(invoice.uid)
            invoice.status = Invoice.Status.CANCELLED
            invoice.save(update_fields=["status"])
            messages.success(request, "Facture annulée.")
        except EmcfError as exc:
            messages.error(request, f"Annulation refusée : {exc}")
    return redirect("invoice_detail", pk=pk)


@login_required
@require_POST
def invoice_delete(request, pk):
    """Supprime un brouillon (jamais envoyé à la DGI). Les autres factures ne se suppriment pas."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if not invoice.is_draft:
        messages.error(request, "Seul un brouillon peut être supprimé.")
        return redirect("invoice_detail", pk=pk)
    invoice.delete()
    messages.success(request, "Brouillon supprimé.")
    return redirect("invoice_list")


@login_required
@require_POST
def invoice_payment(request, pk):
    """Change le statut de paiement (suivi manuel : l'application ne gère pas encore de règlements)."""
    invoice = get_object_or_404(Invoice, pk=pk)
    new_status = request.POST.get("payment_status", "")
    if new_status not in dict(Invoice.PaymentStatus.choices):
        messages.error(request, "Statut de paiement inconnu.")
    else:
        invoice.payment_status = new_status
        invoice.save(update_fields=["payment_status"])
        messages.success(request, f"Statut de paiement : {invoice.get_payment_status_display()}.")
    return redirect("invoice_detail", pk=pk)


@login_required
def invoice_print(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    return render(request, "facturation/invoice_print.html", {
        "doc": documents.doc_from_invoice(invoice),
        "qr": _qr_data_uri(invoice.qr_text),
    })


# --------------------------------------------------------------------------- clients
@login_required
def client_list(request):
    q = request.GET.get("q", "").strip()
    qs = Client.objects.annotate(nb_invoices=Count("invoices"))
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(ifu__icontains=q) | Q(contact__icontains=q) | Q(code__icontains=q)
        )
    return render(request, "facturation/client_list.html", {"clients": qs, "q": q})


@login_required
def client_edit(request, pk=None):
    client = get_object_or_404(Client, pk=pk) if pk else None
    form = ClientForm(request.POST or None, instance=client)
    next_url = _safe_next(request)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, f"Client « {saved.name} » enregistré.")
        if next_url:
            return redirect(f"{next_url}?client={saved.pk}")
        return redirect("client_list")
    return render(request, "facturation/client_form.html",
                  {"form": form, "client": client, "next": next_url})


@login_required
@require_POST
def client_delete(request, pk):
    client = get_object_or_404(Client, pk=pk)
    name = client.name
    client.delete()  # les factures déjà émises gardent une copie des informations du client
    messages.success(request, f"Client « {name} » supprimé.")
    return redirect("client_list")


# --------------------------------------------------------------------------- sociétés
@login_required
def company_list(request):
    companies = CompanySettings.objects.annotate(nb_invoices=Count("invoices"))
    return render(request, "facturation/company_list.html", {"companies": companies})


@login_required
def company_edit(request, pk=None):
    company = get_object_or_404(CompanySettings, pk=pk) if pk else None
    form = CompanyForm(request.POST or None, request.FILES or None, instance=company)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, f"Société « {saved.name} » enregistrée.")
        return redirect("company_list")
    return render(request, "facturation/company_form.html", {"form": form, "company": company})


# --------------------------------------------------------------------------- infos API
@login_required
def api_info(request):
    companies = list(CompanySettings.objects.all())
    if not companies:
        messages.warning(request, "Ajoutez d'abord une société.")
        return redirect("company_create")
    selected = request.GET.get("company", "")
    company = next((c for c in companies if str(c.pk) == selected), companies[0])

    client = EmcfClient(company)
    blocks, errors = [], []
    calls = [
        ("Statut de l'API", client.status),
        ("Types de facture", client.invoice_types),
        ("Groupes de taxe", client.tax_groups),
        ("Types de paiement", client.payment_types),
        ("Demandes en attente (côté DGI)", client.pending),
    ]
    for title, func in calls:
        try:
            blocks.append((title, _pretty(func())))
        except EmcfError as exc:
            errors.append(f"{title} : {exc}")
    return render(request, "facturation/api_info.html", {
        "blocks": blocks, "errors": errors, "companies": companies, "company": company,
    })


# --------------------------------------------------------------------------- numéro, édition locale
@login_required
def next_number(request):
    """Numéro proposé pour (société, type) : appelé en JavaScript par le formulaire."""
    company = CompanySettings.objects.filter(pk=request.GET.get("company") or 0).first()
    inv_type = request.GET.get("type", "FV")
    if not company or inv_type not in dict(Invoice.TYPES):
        return JsonResponse({"number": ""})
    return JsonResponse({"number": next_internal_number(company, inv_type)})


@login_required
def invoice_edit(request, pk):
    """Modifie les champs propres au logiciel. Le contenu fiscal envoyé à la DGI reste inchangé."""
    invoice = get_object_or_404(Invoice, pk=pk)
    initial = {
        "internal_number": invoice.internal_number,
        "payment_communication": invoice.payment_communication,
        "note": invoice.note,
        "amount_remaining": invoice.amount_remaining,
    }
    form = InvoiceInfoForm(request.POST or None, invoice=invoice, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        invoice.internal_number = d["internal_number"]
        invoice.payment_communication = d["payment_communication"]
        invoice.note = d["note"]
        invoice.amount_remaining = d["amount_remaining"]
        invoice.save(update_fields=["internal_number", "payment_communication", "note", "amount_remaining"])
        messages.success(request, "Facture mise à jour.")
        return redirect("invoice_detail", pk=pk)
    return render(request, "facturation/invoice_edit.html", {"form": form, "invoice": invoice})


# --------------------------------------------------------------------------- synchronisation DGI
def _dec(value, default=Decimal(0)):
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _pending_uids(data):
    """Extrait les UID de la réponse de GET /api/invoice (pendingRequestsList)."""
    items = []
    if isinstance(data, dict):
        for key in ("pendingRequestsList", "pendingRequests", "invoices"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    uids = []
    for it in items:
        uid = it.get("uid") if isinstance(it, dict) else it
        if uid:
            uids.append(str(uid))
    return uids


def _create_from_api(company, uid, details, user):
    """Crée en base locale une facture en attente à partir des détails renvoyés par la DGI."""
    details = details if isinstance(details, dict) else {}
    inv_type = details.get("type") if details.get("type") in dict(Invoice.TYPES) else "FV"
    aib = details.get("aib") if details.get("aib") in ("A", "B") else ""
    cl = details.get("client") if isinstance(details.get("client"), dict) else {}

    items = []
    for it in details.get("items") or []:
        if not isinstance(it, dict) or not it.get("name"):
            continue
        group = it.get("taxGroup") if it.get("taxGroup") in dict(TAX_GROUPS) else "B"
        items.append((str(it["name"])[:200], _dec(it.get("price")), _dec(it.get("quantity"), Decimal(1)), group))
    pays = []
    for p in details.get("payment") or []:
        if isinstance(p, dict):
            method = p.get("name") if p.get("name") in dict(PAYMENT_METHODS) else "AUTRE"
            pays.append((method, _dec(p.get("amount"))))

    total = sum((price * qty for _, price, qty, _ in items), Decimal(0))
    number = next_internal_number(company, inv_type)
    with transaction.atomic():
        invoice = Invoice.objects.create(
            company=company, uid=uid, type=inv_type, aib=aib,
            reference=str(details.get("reference") or "")[:64],
            internal_number=number, payment_communication=number,
            client_name=str(cl.get("name") or "")[:200], client_ifu=str(cl.get("ifu") or "")[:13],
            client_contact=str(cl.get("contact") or "")[:100], client_address=str(cl.get("address") or ""),
            total=total, request_payload=details, api_details=details, created_by=user,
        )
        for position, (name, price, qty, group) in enumerate(items):
            InvoiceItem.objects.create(
                invoice=invoice, position=position, name=name, price=price, quantity=qty, tax_group=group
            )
        for method, amount in pays:
            Payment.objects.create(invoice=invoice, method=method, amount=amount)
        invoice.recompute_totals()
    return invoice


@login_required
@require_POST
def invoice_sync(request):
    """Récupère auprès de la DGI les factures EN ATTENTE absentes de la base locale.

    L'API e-MCF ne permet pas de lister les factures déjà confirmées/annulées : seules les
    factures en attente sont retrouvables (à confirmer ou à annuler). On peut aussi importer
    une facture en attente à partir de son UID.
    """
    company = get_object_or_404(CompanySettings, pk=request.POST.get("company") or 0)
    client = EmcfClient(company)
    uid_manual = request.POST.get("uid", "").strip()

    try:
        uids = [uid_manual] if uid_manual else _pending_uids(client.pending())
        if not uid_manual and not uids:
            messages.info(request, f"Aucune facture en attente côté DGI pour « {company.name} ».")
            return redirect("invoice_list")

        imported, known, failed = 0, 0, []
        for uid in uids:
            if Invoice.objects.filter(uid=uid).exists():
                known += 1
                continue
            try:
                _create_from_api(company, uid, client.details(uid), request.user)
                imported += 1
            except EmcfError as exc:
                failed.append(f"{uid} : {exc}")
    except EmcfError as exc:
        messages.error(request, f"Synchronisation impossible : {exc}")
        return redirect("invoice_list")

    if imported:
        messages.success(request, f"{imported} facture(s) en attente importée(s) depuis la DGI.")
    if known:
        messages.info(request, f"{known} facture(s) déjà présente(s) en base.")
    for line in failed:
        messages.warning(request, f"Import impossible — {line}")
    return redirect("invoice_list")
