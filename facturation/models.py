from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from . import calculations as calc
from . import conf

# Libellés exacts demandés, dans cet ordre. Les codes (A-F) sont inchangés : les données
# existantes et l'API e-MCF continuent de fonctionner sans migration de données.
TAX_GROUPS = [
    ("A", "A — EXONÉRÉ"),
    ("B", "B — TAXABLE 18 %"),
    ("C", "C — EXPORTATION"),
    ("D", "D — TVA RÉGIME D’EXCEPTION 18 %"),
    ("E", "E — RÉGIME TPS"),
    ("F", "F — RÉSERVE"),
]

PAYMENT_METHODS = [
    ("ESPECES", "Espèces"),
    ("VIREMENT", "Virement"),
    ("CARTEBANCAIRE", "Carte bancaire"),
    ("MOBILEMONEY", "Mobile money"),
    ("CHEQUES", "Chèque"),
    ("CREDIT", "Crédit"),
    ("AUTRE", "Autre"),
]


class CompanySettings(models.Model):
    """Une société émettrice (vous pouvez en avoir plusieurs)."""

    ENV_CHOICES = [
        ("", "Par défaut de l'application (.env)"),
        ("test", "Test (SyGMEF-test)"),
        ("prod", "Production"),
    ]

    name = models.CharField("Raison sociale", max_length=200, default="Mon entreprise")
    logo = models.ImageField("Logo", upload_to="logo/", blank=True)
    address = models.TextField("Adresse", blank=True)
    phone = models.CharField("Téléphone", max_length=100, blank=True)
    email = models.EmailField("E-mail", blank=True)
    website = models.CharField("Site web", max_length=200, blank=True)
    ifu = models.CharField("IFU (13 chiffres)", max_length=13, blank=True)
    rccm = models.CharField("RCCM", max_length=100, blank=True)
    bank_details = models.TextField("Coordonnées bancaires", blank=True)
    footer_text = models.TextField("Pied de page / mentions", blank=True)
    accent_color = models.CharField("Couleur principale (titre de la facture)", max_length=7, default="#0d6efd")
    secondary_color = models.CharField("Couleur secondaire (libellés)", max_length=7, default="#1f4e8c")
    operator_name = models.CharField(
        "Nom de l'opérateur envoyé à la DGI", max_length=100, default="Administrateur"
    )
    invoice_prefix = models.CharField("Préfixe des factures", max_length=10, default="FAC")
    credit_prefix = models.CharField("Préfixe des avoirs", max_length=10, default="AV")
    emcf_token = models.TextField("Jeton API e-MCF de cette société", blank=True, default="")
    emcf_env = models.CharField(
        "Environnement e-MCF", max_length=4, choices=ENV_CHOICES, blank=True, default=""
    )

    class Meta:
        verbose_name = "Société"
        verbose_name_plural = "Sociétés"
        ordering = ["name"]

    def __str__(self):
        return self.name

    # Le jeton DGI est lié à UN IFU : chaque société a donc son propre jeton.
    # S'il est vide, on retombe sur EMCF_TOKEN / EMCF_ENV du fichier .env.
    @property
    def effective_token(self):
        return self.emcf_token or settings.EMCF_TOKEN

    @property
    def effective_env(self):
        return self.emcf_env or settings.EMCF_ENV

    @property
    def is_prod(self):
        return self.effective_env == "prod"

    @property
    def env_label(self):
        return "PRODUCTION" if self.is_prod else "TEST"


class Client(models.Model):
    """Client enregistré, sélectionnable lors de l'émission d'une facture."""

    name = models.CharField("Raison sociale", max_length=200)
    ifu = models.CharField("IFU", max_length=13, blank=True)
    contact = models.CharField("Contact", max_length=100, blank=True)
    address = models.TextField("Adresse", blank=True)
    shipping_address = models.TextField("Adresse d'expédition", blank=True)
    code = models.CharField(
        "Code client", max_length=30, blank=True, default="",
        help_text="Code interne : jamais imprimé sur les factures ni sur les proformas.",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["code"], condition=~models.Q(code=""), name="uniq_client_code"
            )
        ]

    def __str__(self):
        return self.name


class Invoice(models.Model):
    TYPES = [
        ("FV", "Facture de vente"),
        ("FA", "Facture d'avoir"),
        ("EV", "Facture de vente à l'exportation"),
        ("EA", "Facture d'avoir à l'exportation"),
    ]
    AIB_CHOICES = [("", "Aucun")] + [
        (code, d["label"]) for code, d in conf.DEFAULT_AIB.items()
    ]

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        PENDING = "pending", "En attente"
        CONFIRMED = "confirmed", "Confirmée"
        CANCELLED = "cancelled", "Annulée"

    company = models.ForeignKey(
        CompanySettings, null=True, blank=True, on_delete=models.PROTECT,
        related_name="invoices", verbose_name="Société émettrice",
    )
    client = models.ForeignKey(
        Client, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices"
    )
    # Numérotation PROPRE au logiciel (indépendante du code MECeF/DGI)
    internal_number = models.CharField("N° de facture", max_length=50, blank=True, db_index=True)
    payment_communication = models.CharField("Communication de paiement", max_length=200, blank=True)
    note = models.TextField("Note", blank=True)
    uid = models.CharField("UID e-MCF", max_length=64, unique=True, null=True, blank=True)
    type = models.CharField(max_length=2, choices=TYPES, default="FV")
    reference = models.CharField("UID de la facture d'origine", max_length=64, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    class PaymentStatus(models.TextChoices):
        # Statut de règlement : distinct du statut documentaire (brouillon / confirmée…).
        UNPAID = "unpaid", "Non payé"
        IN_PROGRESS = "in_progress", "En paiement"
        PARTIAL = "partial", "Partiellement payé"
        PAID = "paid", "Payé"

    payment_status = models.CharField(
        "Statut de paiement", max_length=12, choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )
    invoice_date = models.DateField("Date de la facture", null=True, blank=True)
    due_date = models.DateField("Date d'échéance", null=True, blank=True)
    shipping_address = models.TextField("Adresse d'expédition", blank=True)
    aib = models.CharField(max_length=1, choices=AIB_CHOICES, blank=True)

    client_name = models.CharField(max_length=200, blank=True)
    client_ifu = models.CharField(max_length=13, blank=True)
    client_contact = models.CharField(max_length=100, blank=True)
    client_address = models.TextField(blank=True)

    # Montants calculés côté serveur (source de vérité). `total` = total TTC de la facture normalisée
    # (celui que connaît la DGI) ; le net à payer (TTC − AIB) est `net_payable`.
    total_ht = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    aib_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    amount_remaining = models.DecimalField(
        "Reste à payer", max_digits=16, decimal_places=2, null=True, blank=True,
        help_text="Montant saisi par l'émetteur, affiché après les totaux s'il est renseigné.",
    )

    request_payload = models.JSONField(default=dict, blank=True)
    create_response = models.JSONField(default=dict, blank=True)
    api_details = models.JSONField(default=dict, blank=True)
    security = models.JSONField("Éléments de sécurité", default=dict, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "internal_number"],
                condition=~models.Q(internal_number=""),
                name="uniq_company_internal_number",
            )
        ]

    def __str__(self):
        return f"{self.get_type_display()} {self.internal_number or self.number or self.uid}"

    @property
    def issuer(self):
        """Société émettrice (les anciennes factures sans société utilisent la première)."""
        return self.company or CompanySettings.objects.first()

    # --- éléments renvoyés par la DGI à la confirmation ---
    @property
    def number(self):
        return self.security.get("codeMECeFDGI", "")

    @property
    def qr_text(self):
        return self.security.get("qrCode", "")

    @property
    def nim(self):
        return self.security.get("nim", "")

    @property
    def counters(self):
        return self.security.get("counters", "")

    @property
    def security_date(self):
        return self.security.get("dateTime", "")

    @property
    def badge_class(self):
        return {
            self.Status.DRAFT: "bg-info text-dark",
            self.Status.PENDING: "bg-warning text-dark",
            self.Status.CONFIRMED: "bg-success",
            self.Status.CANCELLED: "bg-secondary",
        }.get(self.status, "bg-light text-dark")

    @property
    def is_credit_note(self):
        return self.type in ("FA", "EA")

    @property
    def net_payable(self):
        """Total à payer = total TTC − AIB (l'AIB n'est jamais transmis à la DGI)."""
        return self.total - self.aib_amount

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    @property
    def payment_badge_class(self):
        return {
            self.PaymentStatus.UNPAID: "bg-danger",
            self.PaymentStatus.IN_PROGRESS: "bg-info text-dark",
            self.PaymentStatus.PARTIAL: "bg-warning text-dark",
            self.PaymentStatus.PAID: "bg-success",
        }.get(self.payment_status, "bg-light text-dark")

    @property
    def display_date(self):
        """Date de la facture (et non la date technique de création de l'enregistrement)."""
        return self.invoice_date or timezone.localtime(self.created_at).date()

    def rows_for_calc(self):
        """Lignes ordonnées au format attendu par calculations.compute_document."""
        rows = []
        for it in self.items.all():
            row = {"kind": it.kind, "name": it.name}
            if it.kind == InvoiceItem.Kind.ITEM:
                row.update(quantity=it.quantity, tax_group=it.tax_group)
                if it.price_ht is not None:      # facture saisie en HT
                    row["unit_ht"] = it.price_ht
                else:                            # historique : prix TTC uniquement
                    row["unit_ttc"] = it.price
            rows.append(row)
        return rows

    def compute(self):
        """Résultat complet des calculs de cette facture (recalculé depuis ses lignes)."""
        return calc.compute_document(
            self.rows_for_calc(), conf.tax_rates(),
            aib_rate=conf.aib_rate(self.aib), tax_labels=dict(TAX_GROUPS),
        )

    def recompute_totals(self, save=True):
        """Met à jour les montants enregistrés à partir des lignes (le serveur fait foi)."""
        result = self.compute()
        self.total_ht, self.total_tax = result["total_ht"], result["total_tax"]
        self.aib_amount, self.total = result["aib_amount"], result["total_ttc"]
        if save:
            self.save(update_fields=["total_ht", "total_tax", "aib_amount", "total"])
        return result


class InvoiceItem(models.Model):
    """Élément d'une facture : ligne d'article, titre de section ou note (dans l'ordre d'affichage)."""

    class Kind(models.TextChoices):
        ITEM = "item", "Ligne"
        SECTION = "section", "Section"
        NOTE = "note", "Note"

    invoice = models.ForeignKey(Invoice, related_name="items", on_delete=models.CASCADE)
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.ITEM)
    position = models.PositiveIntegerField(default=0)
    name = models.TextField()  # désignation, titre de section ou texte de la note
    price = models.DecimalField("Prix unitaire TTC", max_digits=16, decimal_places=2, default=0)
    price_ht = models.DecimalField(
        "Prix unitaire HT", max_digits=16, decimal_places=2, null=True, blank=True
    )  # vide pour les factures historiques (saisies en TTC)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    tax_group = models.CharField(max_length=1, choices=TAX_GROUPS, default="B")

    class Meta:
        ordering = ["position", "id"]

    @property
    def line_total(self):
        return self.price * self.quantity


class Payment(models.Model):
    invoice = models.ForeignKey(Invoice, related_name="payments", on_delete=models.CASCADE)
    method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default="ESPECES")
    amount = models.DecimalField(max_digits=16, decimal_places=2)


def next_internal_number(company, inv_type, year=None):
    """Prochain numéro interne, ex. FAC/2025/00001 (AV/2025/00001 pour un avoir).

    Basé sur le plus grand numéro existant de la société pour ce préfixe et cette année :
    si vous modifiez un numéro à la main, la suite repart de celui-ci.
    """
    year = year or timezone.localdate().year
    prefix = company.credit_prefix if inv_type in ("FA", "EA") else company.invoice_prefix
    head = f"{prefix}/{year}/"
    best = 0
    existing = Invoice.objects.filter(company=company, internal_number__startswith=head)
    for number in existing.values_list("internal_number", flat=True):
        tail = number[len(head):]
        if tail.isdigit():
            best = max(best, int(tail))
    return f"{head}{best + 1:05d}"
