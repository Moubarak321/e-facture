# Facturation e-MECeF (Django)

Application Django pour émettre, consulter et gérer des factures normalisées via l'API e-MCF
(SyGM-eMCF) de la DGI du Bénin, avec logo et coordonnées de l'entreprise sur les factures.

## Fonctionnalités
- Création de facture (FV, FA, EV, EA), lignes multiples, AIB, client, paiements multiples
- Option « confirmer immédiatement », ou confirmation / annulation ultérieure
- Création d'un avoir pré-rempli depuis une facture confirmée
- Consultation : liste filtrable, détail, actualisation depuis l'API (`GET /invoice/{uid}`)
- Page « Infos API » : statut, types de facture, groupes de taxe, types de paiement, demandes en attente
- Impression / PDF (via le navigateur) avec logo, coordonnées, QR code et éléments de sécurité DGI
- Paramètres entreprise : logo, adresse, téléphone, e-mail, IFU, RCCM, banque, pied de page, couleur

## Installation (Debian)
```bash
sudo apt install -y python3-venv python3-dev
cd emcf_project
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # puis éditez : EMCF_TOKEN, DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS
set -a; source .env; set +a

python manage.py makemigrations facturation
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000
```
Ouvrez http://IP_DU_SERVEUR:8000, connectez-vous, puis :
1. **Entreprise** : renseignez l'IFU (13 chiffres), le logo et les coordonnées.
2. **Infos API** : vérifiez que la connexion et le jeton fonctionnent.
3. **Nouvelle facture** : testez en environnement `test`.

## Passage en production
- `EMCF_ENV=prod` (les factures deviennent réelles) ; un bandeau rouge « PRODUCTION » s'affiche.
- `DJANGO_DEBUG=0`, vraie `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` précis.
- Servez avec gunicorn derrière Nginx en HTTPS ; Nginx doit servir `/media/` (logo) et `/static/`
  (`python manage.py collectstatic`).

## Points à vérifier avec votre Swagger
Le code a été écrit à partir du SDK Python officiel et de bibliothèques tierces, sans accès au
contenu du Swagger. Si l'API refuse une requête :
- **401** : essayez `EMCF_AUTH_PREFIX=` (jeton brut, comme dans l'exemple du SDK) au lieu de `Bearer`.
- **Noms de champs JSON** (`taxGroup`, `payment`, `client`, `aib`, `reference`…) : tout est dans
  `facturation/emcf_client.py`, fonction `build_payload`.
- **Champs supplémentaires exigés** (ex. `taxGroups`) : `EMCF_EXTRA_PAYLOAD` dans `config/settings.py`.
- **Taux de TVA affichés sur l'impression** : `EMCF_TAX_RATES` (affichage uniquement ; les montants
  officiels sont ceux calculés par la DGI).
- Les chemins d'endpoints sont listés en tête de `emcf_client.py`.
