from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

TAX_GROUPS = [(g, f"Groupe {g}") for g in "ABCDEF"]

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
    accent_color = models.CharField("Couleur principale", max_length=7, default="#0d6efd")
    operator_name = models.CharField(
        "Nom de l'opérateur envoyé à la DGI", max_length=100, default="Administrateur"
    )
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
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Invoice(models.Model):
    TYPES = [
        ("FV", "Facture de vente"),
        ("FA", "Facture d'avoir"),
        ("EV", "Facture de vente à l'exportation"),
        ("EA", "Facture d'avoir à l'exportation"),
    ]
    AIB_CHOICES = [("", "Aucun"), ("A", "AIB groupe A (1 %)"), ("B", "AIB groupe B (5 %)"),("C", "AIB groupe C (3 %)")]

    class Status(models.TextChoices):
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
    uid = models.CharField("UID e-MCF", max_length=64, unique=True)
    type = models.CharField(max_length=2, choices=TYPES, default="FV")
    reference = models.CharField("UID de la facture d'origine", max_length=64, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    aib = models.CharField(max_length=1, choices=AIB_CHOICES, blank=True)

    client_name = models.CharField(max_length=200, blank=True)
    client_ifu = models.CharField(max_length=13, blank=True)
    client_contact = models.CharField(max_length=100, blank=True)
    client_address = models.TextField(blank=True)

    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)

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

    def __str__(self):
        return f"{self.get_type_display()} {self.number or self.uid}"

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
            self.Status.PENDING: "bg-warning text-dark",
            self.Status.CONFIRMED: "bg-success",
            self.Status.CANCELLED: "bg-secondary",
        }.get(self.status, "bg-light text-dark")

    @property
    def is_credit_note(self):
        return self.type in ("FA", "EA")

    def tax_summary(self):
        """Récapitulatif par groupe de taxe (prix TTC -> HT/TVA, taux de settings.EMCF_TAX_RATES)."""
        rates = settings.EMCF_TAX_RATES
        groups = {}
        for item in self.items.all():
            g = groups.setdefault(
                item.tax_group,
                {"group": item.tax_group, "rate": rates.get(item.tax_group, 0), "ttc": Decimal(0)},
            )
            g["ttc"] += item.line_total
        for g in groups.values():
            rate = Decimal(g["rate"])
            g["tax"] = (g["ttc"] * rate / (100 + rate)).quantize(Decimal("1"))
            g["ht"] = g["ttc"] - g["tax"]
        return sorted(groups.values(), key=lambda g: g["group"])


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, related_name="items", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    price = models.DecimalField("Prix unitaire TTC", max_digits=16, decimal_places=2)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    tax_group = models.CharField(max_length=1, choices=TAX_GROUPS, default="B")

    @property
    def line_total(self):
        return self.price * self.quantity


class Payment(models.Model):
    invoice = models.ForeignKey(Invoice, related_name="payments", on_delete=models.CASCADE)
    method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default="ESPECES")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
