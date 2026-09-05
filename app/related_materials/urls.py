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
]
