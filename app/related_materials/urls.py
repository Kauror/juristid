"""Routes for «Seotud materjalid», all under the Matter they concern.

Estonian paths, like the rest of the product. One read fragment, one picker,
and six POST-only decisions. Nothing here answers GET with a write.
"""

from django.urls import path

from app.related_materials import views

urlpatterns = [
    path("teemad/<uuid:pk>/seotud/", views.section, name="section"),
    path("teemad/<uuid:pk>/seotud/otsi/", views.picker, name="picker"),
    path("teemad/<uuid:pk>/seotud/lisa/", views.link, name="link"),
    path("teemad/<uuid:pk>/seotud/eemalda/", views.unlink, name="unlink"),
    path("teemad/<uuid:pk>/seotud/taust/lisa/", views.add_background, name="add_background"),
    path(
        "teemad/<uuid:pk>/seotud/taust/eemalda/",
        views.remove_background,
        name="remove_background",
    ),
    path("teemad/<uuid:pk>/seotud/soovitus/peida/", views.dismiss, name="dismiss"),
    path("teemad/<uuid:pk>/seotud/soovitus/taasta/", views.restore, name="restore"),
    # The one route here that hangs off no Matter, because the thing it is
    # about is not one yet. `Uus teema` asks it what the form so far resembles
    # (docs/adr/0087 §4).
    path("teemad/uus/sarnased/", views.draft_suggestions, name="draft_suggestions"),
    # And the search beside it, for the Teema the suggestions did not propose
    # (docs/adr/0150 §3). Read only; the link is made when the Teema is.
    path("teemad/uus/seotud/otsi/", views.draft_picker, name="draft_picker"),
]
