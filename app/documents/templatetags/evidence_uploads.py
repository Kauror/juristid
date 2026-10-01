"""The `accept` list every evidence file input carries, from the server's own.

A template tag rather than a literal, because a literal in five templates is
five lists that the next format added to `app.documents.uploads` would have to
find. The picker then offered one set of files and the server refused another
after they had been sent — which is how a lawyer met `.asice`: chosen without
complaint, then «Faililaiend .asice ei ole lubatud» under `Registreeri arvamus`
(docs/adr/0126).

The forms that render their own widgets read the same constant
(`UPLOAD_ACCEPT`), and `tests/test_signed_containers.py` holds every file input
in the application to it.
"""

from __future__ import annotations

from django import template

from app.documents.uploads import UPLOAD_ACCEPT

register = template.Library()


@register.simple_tag
def upload_accept() -> str:
    return UPLOAD_ACCEPT
