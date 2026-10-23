"""Importe l'historique des factures normalisées d'Odoo (export CSV ou Excel).

    python manage.py import_factures_odoo "C:\\Temp\\factures_esa_v2.csv" --dry-run   # simulation
    python manage.py import_factures_odoo "C:\\Temp\\factures_esa_v2.csv"             # import réel

Le fichier est celui produit par la requête SQL d'export (une ligne par article).
Chaque facture est importée avec le statut « Confirmée » : numéro Odoo (FAC/2024/00001),
UID, code MECeF/DGI, QR code, articles, client et société. Elles servent d'historique
(consultation, impression, avoir) ; elles ne sont PAS renvoyées à la DGI.

Relançable sans risque : une facture dont l'UID existe déjà est ignorée.
"""
import csv
import html
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from facturation.models import Client, CompanySettings, Invoice, InvoiceItem, Payment

from .import_clients import clean, normalize_ifu

TRUE = {"t", "true", "1", "vrai", "oui", "yes", "y"}
PAYMENT_CODES = {"ESPECES", "VIREMENT", "CARTEBANCAIRE", "MOBILEMONEY", "CHEQUES", "CREDIT", "AUTRE"}
REQUIRED = ("reference_facture", "move_type", "uid_fiscal", "designation", "quantite", "ttc_ligne")


def fix_text(value):
    """Répare le texte UTF-8 lu à tort en Windows-1252 (« BÃ©nin » -> « Bénin »)."""
    if not isinstance(value, str) or not re.search("Ã|â€|Â", value):
        return value
    for codec in ("cp1252", "latin-1"):
        try:
            return value.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return value


def text(value):
    """Valeur de cellule -> texte réparé (jamais None)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return fix_text(str(value)).strip()


def dec(value, default=Decimal(0)):
    try:
        return Decimal(text(value).replace(",", ".").replace("\u202f", "").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return default


def parse_dt(value):
    if isinstance(value, datetime):
        return value
    raw = text(value)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def aware(dt):
    return timezone.make_aware(dt) if dt and timezone.is_naive(dt) else dt


def html_to_text(raw):
    """Note HTML d'Odoo -> texte brut sur plusieurs lignes."""
    raw = re.sub(r"<br\s*/?>|</p>|</div>|<hr\s*/?>", "\n", fix_text(raw or ""))
    plain = html.unescape(re.sub(r"<[^>]+>", "", raw)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in plain.splitlines()]
    return re.sub(r"\n{2,}", "\n", "\n".join(ln for ln in lines if ln)).strip()


def parse_qr(qr):
    """QR e-MCF : « F;NIM;CODE;IFU;aaaammjjhhmmss » -> (NIM, date de certification)."""
    parts = qr.split(";")
    nim = parts[1] if len(parts) > 1 else ""
    when = None
    if len(parts) > 4 and re.fullmatch(r"\d{14}", parts[4]):
        when = datetime.strptime(parts[4], "%Y%m%d%H%M%S")
    return nim, when


def read_rows(path):
    """Lit un CSV (UTF-8) ou un Excel ; retourne une liste de dictionnaires {colonne: valeur}."""
    if path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    from openpyxl import load_workbook

    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    for i, row in enumerate(rows):  # l'en-tête n'est pas forcément sur la 1re ligne
        names = [text(c) for c in row]
        if "reference_facture" in names:
            return [dict(zip(names, r)) for r in rows[i + 1:] if any(c not in (None, "") for c in r)]
    raise CommandError("En-tête introuvable : la colonne « reference_facture » est absente.")


class Command(BaseCommand):
    help = "Importe l'historique des factures normalisées depuis un export Odoo (CSV ou Excel)."

    def add_arguments(self, parser):
        parser.add_argument("fichier")
        parser.add_argument("--dry-run", action="store_true", help="Simule sans rien écrire")
        parser.add_argument("--societe", default=None,
                            help="Force la société (nom exact) au lieu de la déduire du fichier")
        parser.add_argument("--notes", action="store_true",
                            help="Importe aussi le champ « narration » d'Odoo comme note "
                                 "(souvent un texte de conditions générales très long)")
        parser.add_argument("--rapport", default="rapport_import_factures.csv")

    # ------------------------------------------------------------------ outils
    def find_company(self, name, ifu, forced):
        if forced:
            return CompanySettings.objects.filter(name__iexact=forced).first()
        found = CompanySettings.objects.filter(ifu=ifu).first() if ifu else None
        return found or (CompanySettings.objects.filter(name__iexact=name).first() if name else None)

    def find_or_create_client(self, row, stats):
        name = text(row.get("client"))
        if not name:
            return None
        ifu, _ = normalize_ifu(text(row.get("ifu_client")))
        client = Client.objects.filter(ifu=ifu).first() if ifu else None
        client = client or Client.objects.filter(name__iexact=name).first()
        if client is None:
            street, city = text(row.get("adresse_client")), text(row.get("ville_client"))
            address = street if city and city in street else ", ".join(p for p in (street, city) if p)
            client = Client.objects.create(
                name=name[:200], ifu=ifu, contact=text(row.get("tel_client"))[:100], address=address
            )
            stats["clients"] += 1
        return client

    # ------------------------------------------------------------------ import
    def handle(self, *args, **opts):
        path = Path(opts["fichier"])
        if not path.exists():
            raise CommandError(f"Fichier introuvable : {path}")
        rows = read_rows(path)
        if not rows:
            raise CommandError("Le fichier ne contient aucune ligne.")
        missing = [c for c in REQUIRED if c not in rows[0]]
        if missing:
            raise CommandError("Colonnes manquantes : " + ", ".join(missing))

        groups = {}
        for r in rows:  # une facture = toutes les lignes ayant le même UID fiscal
            groups.setdefault(text(r.get("uid_fiscal")) or "SANS-UID:" + text(r.get("reference_facture")), []).append(r)

        stats = dict(invoices=0, credit=0, known=0, skipped=0, clients=0)
        issues = []

        with transaction.atomic():
            for uid, lines in groups.items():
                first = lines[0]
                ref = text(first.get("reference_facture"))

                def skip(msg, value=""):
                    issues.append((ref, msg, value))
                    stats["skipped"] += 1

                if uid.startswith("SANS-UID:"):
                    skip("Ignorée : pas d'UID fiscal (facture non normalisée ?)")
                    continue
                if "is_normalized" in first and text(first["is_normalized"]).lower() not in TRUE:
                    skip("Ignorée : facture non normalisée")
                    continue
                if Invoice.objects.filter(uid=uid).exists():
                    stats["known"] += 1
                    continue

                inv_type = {"out_invoice": "FV", "out_refund": "FA"}.get(text(first.get("move_type")))
                state = text(first.get("statut_odoo")) or "posted"
                if not inv_type or state not in ("posted", "cancel"):
                    skip("Ignorée : type ou statut non géré", f"{first.get('move_type')} / {state}")
                    continue

                company = self.find_company(
                    text(first.get("societe")), text(first.get("ifu_societe")), opts["societe"]
                )
                if company is None:
                    skip("Ignorée : société introuvable dans l'application (créez-la d'abord)",
                         f"{text(first.get('societe'))} / IFU {text(first.get('ifu_societe'))}")
                    continue

                number = ref
                if number and Invoice.objects.filter(company=company, internal_number=number).exists():
                    skip("Ignorée : ce n° de facture existe déjà pour cette société", number)
                    continue

                # --- articles : prix TTC unitaire = TTC de la ligne / quantité, groupe de taxe d'après le taux
                items, ttc_sum = [], Decimal(0)
                for r in lines:
                    qty, ttc, ht = dec(r.get("quantite")), dec(r.get("ttc_ligne")), dec(r.get("ht_ligne"))
                    name = text(r.get("designation"))
                    if qty <= 0 or not name:
                        continue
                    rate = int(((ttc / ht - 1) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if ht else 0
                    if rate not in (0, 18):
                        issues.append((ref, f"Taux de {rate} % inconnu : groupe B utilisé", name[:60]))
                    items.append((name[:200], (ttc / qty).quantize(Decimal("0.01")), qty, "A" if rate == 0 else "B"))
                    ttc_sum += ttc
                if not items:
                    skip("Ignorée : aucune ligne d'article exploitable")
                    continue

                total = dec(first.get("amount_total"), ttc_sum)
                if abs(total - ttc_sum) > 1:
                    issues.append((ref, "Total de la facture ≠ somme des lignes (importée quand même)",
                                   f"{total} / {ttc_sum}"))

                # --- éléments de sécurité DGI
                qr = text(first.get("secu_qrcode"))
                nim, certified = parse_qr(qr)
                created = parse_dt(first.get("date_facture")) or certified or parse_dt(first.get("create_date"))
                client = self.find_or_create_client(first, stats)
                street, city = text(first.get("adresse_client")), text(first.get("ville_client"))
                method = text(first.get("norma_payment_type")).upper()
                note = html_to_text(first.get("narration")) if opts["notes"] else ""

                with transaction.atomic():
                    invoice = Invoice.objects.create(
                        company=company, client=client, uid=uid, type=inv_type,
                        status=Invoice.Status.CANCELLED if state == "cancel" else Invoice.Status.CONFIRMED,
                        internal_number=number,
                        payment_communication=(text(first.get("payment_reference")) or number)[:200],
                        note=note, reference=text(first.get("uid_facture_origine"))[:64],
                        client_name=text(first.get("client"))[:200],
                        client_ifu=normalize_ifu(text(first.get("ifu_client")))[0],
                        client_contact=text(first.get("tel_client"))[:100],
                        client_address=street if city and city in street else ", ".join(p for p in (street, city) if p),
                        total=total, created_at=aware(created) if created else timezone.now(),
                        confirmed_at=aware(certified) if certified else None,
                        request_payload={"source": "odoo", "id_odoo": text(first.get("id_odoo"))},
                        security={
                            "codeMECeFDGI": text(first.get("secu_codemecefdgi")), "qrCode": qr,
                            "nim": nim, "counters": "",
                            "dateTime": certified.isoformat() if certified else "",
                        },
                    )
                    for name, price, qty, group in items:
                        InvoiceItem.objects.create(
                            invoice=invoice, name=name, price=price, quantity=qty, tax_group=group
                        )
                    Payment.objects.create(
                        invoice=invoice, method=method if method in PAYMENT_CODES else "AUTRE", amount=total
                    )
                stats["credit" if inv_type == "FA" else "invoices"] += 1

            if opts["dry_run"]:
                transaction.set_rollback(True)

        if issues:
            with open(opts["rapport"], "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Facture", "Anomalie", "Valeur"])
                w.writerows(issues)

        mode = "SIMULATION (rien n'a été enregistré)" if opts["dry_run"] else "IMPORT TERMINÉ"
        self.stdout.write(self.style.SUCCESS(mode))
        self.stdout.write(f"  Factures dans le fichier : {len(groups)}")
        self.stdout.write(f"  Factures importées       : {stats['invoices']}")
        self.stdout.write(f"  Avoirs importés          : {stats['credit']}")
        self.stdout.write(f"  Déjà présentes           : {stats['known']}")
        self.stdout.write(f"  Ignorées                 : {stats['skipped']}")
        self.stdout.write(f"  Clients créés            : {stats['clients']}")
        self.stdout.write(f"  Anomalies                : {len(issues)}"
                          + (f"  (détail dans {opts['rapport']})" if issues else ""))
