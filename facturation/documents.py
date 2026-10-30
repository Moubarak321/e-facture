"""Construit le « document » affiché par la facture imprimée, la proforma et l'aperçu avant enregistrement.

Un seul gabarit (invoice_print.html) sert aux factures enregistrées et à la prévisualisation
d'une facture en cours d'édition : les deux passent par ces fonctions et par les mêmes calculs.
Le code client n'est jamais transmis au document.

Deux versions du même document :
  * « proforma » : titre « Facture proforma », filigrane PROFORMA, sans éléments de sécurité DGI ;
  * « facture »  : sans aucune mention proforma. Elle comporte les éléments de sécurité DGI quand la
                   facture est confirmée (= facture normalisée) ; un brouillon ou une facture en
                   attente n'en a pas (un avertissement s'affiche alors à l'écran, jamais à l'impression).
"""
from . import conf
from .models import Invoice
from .numwords import amount_in_words

MODES = ("facture", "proforma")


def _document(*, company, type_code, number, mode, banner, banner_class, invoice_date, due_date,
              client, shipping_address, result, aib_code, communication, note, remaining=None,
              security=None, preview=False, notice=""):
    credit = type_code in ("FA", "EA")
    kind = "Avoir" if credit else "Facture"
    proforma = mode == "proforma"
    title = f"{kind} proforma" if proforma else kind
    aib_label = conf.aib_definitions().get(aib_code, {}).get("label", "") if aib_code else ""
    return {
        "company": company,
        "mode": mode,
        "title": title,
        "file_title": f"{title} {number}".strip().replace("/", "-"),   # nom de fichier proposé à l'export PDF
        "number": number,
        "proforma": proforma,
        "preview": preview,                    # aperçu d'une facture non enregistrée
        "notice": notice,                      # avertissement affiché à l'écran uniquement
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
        "remaining": remaining,                # « Reste à payer » saisi par l'émetteur (ou None)
        "words": amount_in_words(result["total_payable"], conf.CURRENCY_WORDS),
        "currency": conf.CURRENCY_SYMBOL,
        "communication": communication,
        "note": note,
        "security": security,
    }


def doc_from_invoice(invoice, mode=None):
    """Document d'une facture enregistrée. Par défaut : proforma pour un brouillon, facture sinon."""
    status = invoice.status
    if mode not in MODES:
        mode = "proforma" if status == Invoice.Status.DRAFT else "facture"

    banner, banner_class = "", ""
    if mode == "proforma":
        banner, banner_class = "FACTURE PROFORMA : document sans valeur fiscale", "warn"
    elif status == Invoice.Status.PENDING:
        banner = "FACTURE NON CONFIRMÉE : document sans valeur fiscale tant qu'elle n'est pas confirmée auprès de la DGI."
        banner_class = "warn"
    elif status == Invoice.Status.CANCELLED:
        banner, banner_class = "FACTURE ANNULÉE", "cancelled"

    security = None
    if mode == "facture" and invoice.security:      # éléments de sécurité DGI : jamais sur une proforma
        security = {"code": invoice.number, "nim": invoice.nim, "counters": invoice.counters,
                    "date": invoice.security_date, "qr_text": invoice.qr_text}
    notice = ""
    if mode == "facture" and not invoice.security:
        notice = (f"Aperçu : cette facture n'est pas confirmée (statut « {invoice.get_status_display()} »), "
                  "elle ne comporte donc pas les éléments de sécurité de la DGI.")

    return _document(
        company=invoice.issuer, type_code=invoice.type, number=invoice.internal_number, mode=mode,
        banner=banner, banner_class=banner_class,
        invoice_date=invoice.display_date, due_date=invoice.due_date,
        client={"name": invoice.client_name, "ifu": invoice.client_ifu,
                "contact": invoice.client_contact, "address": invoice.client_address},
        shipping_address=invoice.shipping_address, result=invoice.compute(), aib_code=invoice.aib,
        communication=invoice.payment_communication, note=invoice.note,
        remaining=invoice.amount_remaining, security=security, notice=notice,
    )


def doc_from_form(company, client, data, result, mode="proforma"):
    """Aperçu d'une facture en cours d'édition (rien n'est enregistré) : proforma ou facture."""
    proforma = mode == "proforma"
    return _document(
        company=company, type_code=data["type"], number=(data.get("internal_number") or "").strip(),
        mode=mode, banner="FACTURE PROFORMA : document sans valeur fiscale" if proforma else "",
        banner_class="warn" if proforma else "",
        invoice_date=data["invoice_date"], due_date=data.get("due_date"),
        client={"name": client.name if client else "", "ifu": client.ifu if client else "",
                "contact": client.contact if client else "", "address": client.address if client else ""},
        shipping_address=data.get("shipping_address", ""), result=result, aib_code=data.get("aib"),
        communication=(data.get("payment_communication") or "").strip(),
        note=data.get("note", ""), remaining=data.get("amount_remaining"), preview=True,
        notice="" if proforma else (
            "Aperçu d'une facture non enregistrée : elle ne comporte pas les éléments de sécurité de la DGI. "
            "Utilisez « Confirmer la facture » pour la normaliser."),
    )
