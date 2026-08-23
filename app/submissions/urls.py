"""Arvamused: the workspace, then the Matter-scoped write actions.

The two index routes come first and are exact paths, so neither can shadow the
``teema/<uuid>/…`` actions below them. `/arvamused/` was already the mount point
for the write surfaces; giving it a page is what turns it into a destination
somebody can navigate to rather than a prefix that only ever appeared in a form
action.
"""

from django.urls import path

from app.submissions import views, workspace_views

urlpatterns = [
    path("", workspace_views.sent, name="sent"),
    path("arhiiv/", workspace_views.archive, name="archive"),
    path("teema/<uuid:matter_id>/uus/", views.create, name="create"),
    path("<uuid:pk>/toend/", views.attach_evidence, name="attach_evidence"),
    path("<uuid:pk>/saada/", views.mark_sent, name="mark_sent"),
    path("<uuid:pk>/tagasi/", views.withdraw, name="withdraw"),
]
