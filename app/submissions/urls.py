"""Arvamused: the workspace, then the Matter-scoped write actions.

The reading routes come first and are exact paths, so none can shadow the
``teema/<uuid>/…`` actions below them. `/arvamused/` was already the mount point
for the write surfaces; giving it a page is what turns it into a destination
somebody can navigate to rather than a prefix that only ever appeared in a form
action.

Since ``docs/adr/0047`` the workspace is no longer offered on the bar — it is a
section of the Teemad page. Nothing here changed for that: every route below
still resolves, still carries its own filters and its own pager, and is still
where «Vaata kõiki arvamusi» leads. What was added is one more exact path,
``plokk/``, which answers the embedded section's own live search.
"""

from django.urls import path

from app.submissions import views, workspace_views

urlpatterns = [
    path("", workspace_views.sent, name="sent"),
    path("arhiiv/", workspace_views.archive, name="archive"),
    # The Arvamused section on the Teemad page, as a fragment.
    #
    # A route of its own rather than an `HX-Request` branch on the register,
    # because the register already has one: `matters.views._wants_fragment`
    # answers *any* HTMX request to `/teemad/` with the register's results, so
    # an opinion search made against that URL would come back as a table of
    # teemad. Two fragments on one address need two addresses.
    #
    # Never pushed into the address bar — the fragment is swapped into a page
    # whose own URL is `/teemad/`, and that is the URL a reader must keep
    # (docs/adr/0047).
    path("plokk/", workspace_views.embedded_block, name="embedded_block"),
    path("teema/<uuid:matter_id>/uus/", views.create, name="create"),
    path("<uuid:pk>/toend/", views.attach_evidence, name="attach_evidence"),
    path("<uuid:pk>/saada/", views.mark_sent, name="mark_sent"),
    path("<uuid:pk>/tagasi/", views.withdraw, name="withdraw"),
]
