"""Search routes.

Two, over one search. ``search`` is the page a submitted form reaches;
``suggestions`` is the same ranked, authorized result set, five rows of it, for
the dropdown under the header field. Both go through
``app.search.services``, so neither can answer a question the other would
refuse (app/search/views.py).
"""

from django.urls import path

from app.search import views

urlpatterns = [
    path("", views.search_view, name="search"),
    path("soovitused/", views.suggestions, name="suggestions"),
]
