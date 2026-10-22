import base64
import io
import json
from decimal import ROUND_HALF_UP, Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .emcf_client import EmcfClient, EmcfError, build_payload
from .forms import ClientForm, CompanyForm, InvoiceForm, ItemFormSet, PaymentFormSet
from .models import Client, CompanySettings, Invoice, InvoiceItem, Payment


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
    if q:
        qs = qs.filter(
            Q(client_name__icontains=q) | Q(uid__icontains=q) | Q(client_ifu__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    if inv_type:
        qs = qs.filter(type=inv_type)
    if company_id.isdigit():
        qs = qs.filter(company_id=company_id)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "facturation/invoice_list.html", {
        "page": page, "q": q, "status": status, "inv_type": inv_type, "company_id": company_id,
        "statuses": Invoice.Status.choices, "types": Invoice.TYPES,
        "companies": CompanySettings.objects.all(),
    })


# --------------------------------------------------------------------------- création de facture
def _confirm_invoice(invoice):
    data = EmcfClient(invoice.issuer).confirm(invoice.uid)
    invoice.security = data
    invoice.status = Invoice.Status.CONFIRMED
    invoice.confirmed_at = timezone.now()
    invoice.save()


def _invoice_form_response(request, form, items, pays):
    return render(request, "facturation/invoice_form.html",
                  {"form": form, "items": items, "pays": pays})


@login_required
def invoice_create(request):
    if not CompanySettings.objects.exists():
        messages.warning(request, "Ajoutez d'abord la société qui émettra les factures.")
        return redirect("company_create")

    if request.method != "POST":
        initial = {}
        source = None
        if request.GET.get("from"):
            source = get_object_or_404(Invoice, pk=request.GET["from"])
        if source:  # avoir pré-rempli à partir d'une facture
            issuer = source.issuer
            initial.update({
                "company": issuer.pk if issuer else None,
                "client": source.client_id,
                "type": "EA" if source.type in ("EV", "EA") else "FA",
                "reference": source.uid,
                "aib": source.aib,
            })
            items = ItemFormSet(prefix="items", initial=[
                {"name": i.name, "price": i.price, "quantity": i.quantity, "tax_group": i.tax_group}
                for i in source.items.all()
            ])
        else:
            if request.GET.get("company"):
                initial["company"] = request.GET["company"]
            elif CompanySettings.objects.count() == 1:
                initial["company"] = CompanySettings.objects.first().pk
            if request.GET.get("client"):
                initial["client"] = request.GET["client"]
            items = ItemFormSet(prefix="items")
        return _invoice_form_response(
            request, InvoiceForm(initial=initial), items, PaymentFormSet(prefix="pays")
        )

    form = InvoiceForm(request.POST)
    items = ItemFormSet(request.POST, prefix="items")
    pays = PaymentFormSet(request.POST, prefix="pays")
    if not (form.is_valid() and items.is_valid() and pays.is_valid()):
        return _invoice_form_response(request, form, items, pays)

    d = form.cleaned_data
    company, client = d["company"], d["client"]
    if not company.ifu:
        messages.error(request, f"Renseignez d'abord l'IFU de la société « {company.name} ».")
        return _invoice_form_response(request, form, items, pays)

    item_rows = [
        f.cleaned_data for f in items.forms
        if f.cleaned_data and not f.cleaned_data.get("DELETE")
    ]
    pay_rows = [
        f.cleaned_data for f in pays.forms
        if f.cleaned_data and not f.cleaned_data.get("DELETE") and f.cleaned_data.get("amount")
    ]
    total = sum((r["price"] * r["quantity"] for r in item_rows), Decimal(0))
    total = total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    if pay_rows:
        paid = sum((p["amount"] for p in pay_rows), Decimal(0))
        if paid != total:
            messages.error(
                request,
                f"La somme des paiements ({paid}) doit être égale au total de la facture ({total}).",
            )
            return _invoice_form_response(request, form, items, pays)
    else:
        pay_rows = [{"method": "ESPECES", "amount": total}]

    client_data = {
        "client_name": client.name if client else "",
        "client_ifu": client.ifu if client else "",
        "client_contact": client.contact if client else "",
        "client_address": client.address if client else "",
    }
    payload = build_payload(
        company.ifu, company.operator_name, {**d, **client_data}, item_rows, pay_rows
    )

    try:
        resp = EmcfClient(company).create(payload)
    except EmcfError as exc:
        messages.error(request, f"Facture rejetée par l'API e-MCF : {exc}")
        return _invoice_form_response(request, form, items, pays)

    uid = resp.get("uid") if isinstance(resp, dict) else None
    if not uid:
        messages.error(request, f"Réponse inattendue de l'API (pas d'uid) : {resp}")
        return _invoice_form_response(request, form, items, pays)

    api_total = resp.get("total")
    try:
        total_saved = Decimal(str(api_total)) if api_total is not None else total
    except Exception:
        total_saved = total

    with transaction.atomic():
        invoice = Invoice.objects.create(
            company=company, client=client, uid=uid, type=d["type"],
            reference=d["reference"], aib=d["aib"], total=total_saved,
            request_payload=payload, create_response=resp, created_by=request.user,
            **client_data,
        )
        for r in item_rows:
            InvoiceItem.objects.create(
                invoice=invoice, name=r["name"], price=r["price"],
                quantity=r["quantity"], tax_group=r["tax_group"],
            )
        for p in pay_rows:
            Payment.objects.create(invoice=invoice, method=p["method"], amount=p["amount"])

    messages.success(request, "Facture envoyée à la DGI (en attente de confirmation).")
    if d["confirm_now"]:
        try:
            _confirm_invoice(invoice)
            messages.success(request, "Facture confirmée.")
        except EmcfError as exc:
            messages.warning(request, f"Confirmation impossible : {exc}")
    return redirect("invoice_detail", pk=invoice.pk)


# --------------------------------------------------------------------------- détail & actions
@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("company", "client"), pk=pk)
    return render(request, "facturation/invoice_detail.html", {
        "invoice": invoice,
        "payload_json": _pretty(invoice.request_payload),
        "details_json": _pretty(invoice.api_details) if invoice.api_details else "",
        "security_json": _pretty(invoice.security) if invoice.security else "",
    })


@login_required
@require_POST
def invoice_refresh(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
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
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status != Invoice.Status.PENDING:
        messages.warning(request, "Seule une facture en attente peut être confirmée.")
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
    if invoice.status != Invoice.Status.PENDING:
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
def invoice_print(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    return render(request, "facturation/invoice_print.html", {
        "invoice": invoice,
        "company": invoice.issuer,
        "qr": _qr_data_uri(invoice.qr_text),
        "tax_summary": invoice.tax_summary(),
    })


# --------------------------------------------------------------------------- clients
@login_required
def client_list(request):
    q = request.GET.get("q", "").strip()
    qs = Client.objects.annotate(nb_invoices=Count("invoices"))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(ifu__icontains=q) | Q(contact__icontains=q))
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
