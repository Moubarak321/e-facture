"""Client HTTP pour l'API e-MCF (SyGM-eMCF) de la DGI du Bénin.

Endpoints utilisés (d'après le SDK officiel) :
    GET  /api/info/status | invoiceTypes | taxGroups | paymentTypes
    GET  /api/invoice                    -> demandes en attente
    POST /api/invoice                    -> création (renvoie l'uid)
    GET  /api/invoice/{uid}              -> détails
    PUT  /api/invoice/{uid}/confirm      -> finalisation (QR code, code MECeF/DGI...)
    PUT  /api/invoice/{uid}/cancel       -> annulation d'une facture en attente
"""
from decimal import Decimal

import requests
from django.conf import settings


class EmcfError(Exception):
    def __init__(self, message, status_code=None, error_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.body = body


class EmcfClient:
    def __init__(self, company=None, token=None, env=None, timeout=30):
        """`company` : société (CompanySettings) dont on utilise le jeton et l'environnement."""
        if company is not None:
            token = company.effective_token if token is None else token
            env = env or company.effective_env
        self.token = settings.EMCF_TOKEN if token is None else token
        self.env = env or settings.EMCF_ENV
        self.base_url = settings.EMCF_URLS[self.env].rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ HTTP
    def _headers(self):
        prefix = settings.EMCF_AUTH_PREFIX.strip()
        value = f"{prefix} {self.token}" if prefix else self.token
        return {"Authorization": value, "Accept": "application/json"}

    def _request(self, method, path, payload=None):
        if not self.token:
            raise EmcfError(
                "Aucun jeton configuré pour cette société (ni EMCF_TOKEN dans le .env)."
            )
        url = f"{self.base_url}/api{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), json=payload, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise EmcfError(f"Connexion impossible à l'API e-MCF : {exc}") from exc

        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {"raw": resp.text[:500]}

        error_code = body.get("errorCode") if isinstance(body, dict) else None
        if resp.status_code >= 400 or error_code:
            desc = body.get("errorDesc") if isinstance(body, dict) else None
            if resp.status_code == 401:
                desc = desc or "Jeton refusé (401). Vérifiez EMCF_TOKEN / EMCF_AUTH_PREFIX."
            message = desc or f"Erreur HTTP {resp.status_code}"
            if error_code:
                message = f"[{error_code}] {message}"
            raise EmcfError(message, resp.status_code, error_code, body)
        return body

    # ------------------------------------------------------------------ INFO
    def status(self):
        return self._request("GET", "/info/status")

    def invoice_types(self):
        return self._request("GET", "/info/invoiceTypes")

    def tax_groups(self):
        return self._request("GET", "/info/taxGroups")

    def payment_types(self):
        return self._request("GET", "/info/paymentTypes")

    # --------------------------------------------------------------- INVOICE
    def pending(self):
        return self._request("GET", "/invoice")

    def create(self, payload):
        return self._request("POST", "/invoice", payload)

    def details(self, uid):
        return self._request("GET", f"/invoice/{uid}")

    def confirm(self, uid):
        return self._request("PUT", f"/invoice/{uid}/confirm")

    def cancel(self, uid):
        return self._request("PUT", f"/invoice/{uid}/cancel")


def _num(value):
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def build_payload(ifu, operator_name, data, items, payments):
    """Construit le JSON de création de facture.

    Si le Swagger demande d'autres noms de champs, c'est ICI qu'il faut les ajuster.
    """
    payload = {
        "ifu": ifu,
        "type": data["type"],
        "operator": {"name": operator_name},
        "items": [
            {
                "name": it["name"],
                "price": _num(it["price"]),
                "quantity": _num(it["quantity"]),
                "taxGroup": it["tax_group"],
            }
            for it in items
        ],
        "payment": [{"name": p["method"], "amount": _num(p["amount"])} for p in payments],
    }
    client = {
        "name": data.get("client_name"),
        "ifu": data.get("client_ifu"),
        "contact": data.get("client_contact"),
        "address": data.get("client_address"),
    }
    client = {k: v for k, v in client.items() if v}
    if client:
        payload["client"] = client
    if data.get("aib"):
        payload["aib"] = data["aib"]
    if data.get("reference"):
        payload["reference"] = data["reference"]
    payload.update(settings.EMCF_EXTRA_PAYLOAD)
    return payload
