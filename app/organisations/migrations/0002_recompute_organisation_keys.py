"""Recompute the organisation matching keys under the organisation key (ENG-045).

`Organisation.normalized_name` and `OrganisationAlias.normalized_alias` are
derived columns: each save writes them from the name, and nothing else ever
reads them except the lookup that compares a typed name with them. The fold
that writes them now ignores Unicode's invisible format characters (category
Cf) — a pasted soft hyphen, zero-width space or byte-order mark no longer makes
a second institution — so a row stored under the earlier fold would carry a key
no lookup produces any more, and the name it was saved under would create a
duplicate beside it.

**A recompute of the derived columns and nothing else.** No name, no type, no
alias text and no timestamp is written; no row is created, deleted or merged.
Rows whose name holds no format character get exactly the key they already had,
so they are not written at all. Two institutions whose names now fold to one key
are *not* merged here — the lookup refuses that name as ambiguous until a person
merges them, and `manage.py check_organisation_duplicates` lists them.

**One case is left alone rather than forced.** One institution can hold two
aliases that differed only by a format character, and under the new key they
would be one — which the per-institution alias constraint forbids. The second
keeps its old key; its institution is still found through the first. The
duplicate report counts it as a stale key.

**Reversible exactly.** The reverse writes the earlier fold's key back, from the
same name, with the same rule for a colliding alias.

Both folds are written out below rather than imported. A migration has to keep
doing what it did on the day it was written, whatever `app.core.text` becomes.
"""

import re
import unicodedata

from django.db import migrations

_WHITESPACE = re.compile(r"\s+")
_BATCH = 500


def _earlier_fold(value):
    """`app.core.text.normalize_for_matching` as it stands in this release."""
    if not value:
        return ""
    lowered = unicodedata.normalize("NFKD", value.strip().casefold())
    without_marks = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    return _WHITESPACE.sub(" ", without_marks).strip()


def _organisation_key(value):
    """`app.core.text.normalize_organisation_name` as it stands in this release."""
    if not value:
        return ""
    return _earlier_fold("".join(ch for ch in value if unicodedata.category(ch) != "Cf"))


def _recompute(apps, fold):
    Organisation = apps.get_model("organisations", "Organisation")
    OrganisationAlias = apps.get_model("organisations", "OrganisationAlias")

    changed = []
    for organisation in Organisation.objects.only("pk", "name", "normalized_name").iterator(
        chunk_size=2000
    ):
        key = fold(organisation.name)
        if key != organisation.normalized_name:
            organisation.normalized_name = key
            changed.append(organisation)
    Organisation.objects.bulk_update(changed, ["normalized_name"], batch_size=_BATCH)

    aliases = list(
        OrganisationAlias.objects.only("pk", "organisation_id", "alias", "normalized_alias")
        .order_by("created_at", "pk")
        .iterator(chunk_size=2000)
    )
    # What each institution's aliases will hold once this runs: every alias
    # whose key does not change, then each changed one in the order they were
    # recorded, unless an earlier one already claimed that key.
    held = {
        (alias.organisation_id, alias.normalized_alias)
        for alias in aliases
        if fold(alias.alias) == alias.normalized_alias
    }
    changed = []
    for alias in aliases:
        key = fold(alias.alias)
        if key == alias.normalized_alias:
            continue
        if (alias.organisation_id, key) in held:
            continue
        held.add((alias.organisation_id, key))
        alias.normalized_alias = key
        changed.append(alias)
    OrganisationAlias.objects.bulk_update(changed, ["normalized_alias"], batch_size=_BATCH)


def forwards(apps, schema_editor):
    _recompute(apps, _organisation_key)


def backwards(apps, schema_editor):
    _recompute(apps, _earlier_fold)


class Migration(migrations.Migration):
    dependencies = [
        ("organisations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
