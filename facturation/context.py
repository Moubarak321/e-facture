from django.conf import settings


def shared(request):
    """Variables disponibles dans tous les templates."""
    return {"emcf_env": settings.EMCF_ENV}
