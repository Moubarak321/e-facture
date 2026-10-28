"""Construit le « document » affiché par la facture imprimée et par la proforma.

Un seul gabarit (invoice_print.html) sert aux factures enregistrées et à la prévisualisation
d'une facture en cours d'édition : les deux passent par ces fonctions et par les mêmes calculs.
Le code client n'est jamais transmis au document.
"""
from . import conf
from .models import Invoice
from .numwords import amount_in_words


def _document(*, company, type_code, number, proforma, banner, banner_class, invoice_date, due_date,
              client, shipping_address, result, aib_code, communication, note, security=None):
    credit = type_code in ("FA", "EA")
    kind = "Avoir" if credit else "Facture"
    aib_label = conf.aib_definitions().get(aib_code, {}).get("label", "") if aib_code else ""
    return {
        "company": company,
        "title": f"{kind} proforma" if proforma else kind,
        "number": number,
        "proforma": proforma,
        "banner": banner,
        "banner_class": banner_class,
        "invoice_date": invoice_date,
        "due_date": due_date,
        "client": client,                      # dict : name, ifu, contact, address (jamais le code)
        "shipping_address": shipping_address,
        "rows": result["rows"],
        "tax_summary": result["tax_summary"],
        "totals": result,
        "aib_label": aib_label,
        "aib_added": conf.AIB_ADDED_TO_TOTAL,
        "words": amount_in_words(result["total_payable"], conf.CURRENCY_WORDS),
        "currency": conf.CURRENCY_SYMBOL,
        "communication": communication,
        "note": note,
        "security": security,
    }


def doc_from_invoice(invoice):
    """Document d'une facture enregistrée (brouillon, en attente, confirmée ou annulée)."""
    status = invoice.status
    banner, banner_class = "", ""
    if status == Invoice.Status.DRAFT:
        banner, banner_class = "FACTURE PROFORMA : brouillon, sans valeur fiscale", "warn"
    elif status == Invoice.Status.PENDING:
        banner = "FACTURE NON CONFIRMÉE : document sans valeur fiscale tant qu'elle n'est pas confirmée auprès de la DGI."
        banner_class = "warn"
    elif status == Invoice.Status.CANCELLED:
        banner, banner_class = "FACTURE ANNULÉE", "cancelled"
    security = None
    if invoice.security:
        security = {"code": invoice.number, "nim": invoice.nim, "counters": invoice.counters,
                    "date": invoice.security_date, "qr_text": invoice.qr_text}
    return _document(
        company=invoice.issuer, type_code=invoice.type, number=invoice.internal_number,
        proforma=status == Invoice.Status.DRAFT, banner=banner, banner_class=banner_class,
        invoice_date=invoice.display_date, due_date=invoice.due_date,
        client={"name": invoice.client_name, "ifu": invoice.client_ifu,
                "contact": invoice.client_contact, "address": invoice.client_address},
        shipping_address=invoice.shipping_address, result=invoice.compute(), aib_code=invoice.aib,
        communication=invoice.payment_communication, note=invoice.note, security=security,
    )


def doc_from_form(company, client, data, result):
    """Proforma d'une facture en cours d'édition (rien n'est enregistré)."""
    return _document(
        company=company, type_code=data["type"], number=(data.get("internal_number") or "").strip(),
        proforma=True, banner="FACTURE PROFORMA : document sans valeur fiscale", banner_class="warn",
        invoice_date=data["invoice_date"], due_date=data.get("due_date"),
        client={"name": client.name if client else "", "ifu": client.ifu if client else "",
                "contact": client.contact if client else "", "address": client.address if client else ""},
        shipping_address=data.get("shipping_address", ""), result=result, aib_code=data.get("aib"),
        communication=(data.get("payment_communication") or "").strip(),
        note=data.get("note", ""),
    )
