from django.conf import settings
from django.urls import path

from app.core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("favicon.ico", views.favicon, name="favicon"),
    path("disainisusteem/", views.design_tokens, name="design_tokens"),
    # The v2 rebuild's own worklist. Under `/haldus/` with the other internal
    # tooling and deliberately not on the bar (app/core/development_status.py).
    path("haldus/arendus/", views.development_status, name="development_status"),
]

# The Teema design-refinement preview, and only in a development configuration.
#
# Registered conditionally rather than gated only in the view: a production
# routing table that does not contain the address cannot resolve it however the
# request arrives. The view refuses again on its own (app/core/design_preview.py)
# — this is the outer of two gates, not the only one.
#
# Preview-only, throwaway, and not a product surface. It renders a design study
# of `/teemad/<pk>/`; the Matter page itself is untouched.
if settings.DEBUG and not settings.REAL_DATA_ALLOWED:
    from app.core import design_preview

    urlpatterns += [
        path(
            "disainisusteem/teema-refinement/<uuid:pk>/",
            design_preview.matter_refinement,
            name="matter_refinement_preview",
        ),
    ]
