"""Public reference data about Estonian institutions.

These are the ministries as they are currently named. The list is public
information, not Koda data, which is why it lives here rather than in the
synthetic development seed: the same rows belong in a future secure deployment,
where inventing `Näidisministeerium` would be useless.

Two rules the seeding obeys, both of them about not destroying history.

**Never merge a rename with a change of remit.** ``Keskkonnaministeerium`` and
``Kliimaministeerium`` look like the same institution renamed. They are not:
the remit changed, and a Matter filed under one is not automatically a Matter
of the other. The predecessor links below record only relationships that are
genuinely a continuation, and nothing here alters an existing Organisation's
identity.

**Never rewrite what is already there.** Seeding adds missing rows and missing
aliases. It does not rename, retype, deactivate or delete an Organisation that
already exists, because by the time this runs a second time somebody may have
edited one deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.organisations.models import OrganisationType


@dataclass(frozen=True)
class ReferenceOrganisation:
    name: str
    organisation_type: str
    #: Abbreviations and spellings the register and everyday use actually
    #: contain. These make search and import matching work without any fuzzy
    #: comparison — an alias is a recorded decision, not a guess.
    aliases: tuple[str, ...] = field(default_factory=tuple)


#: The eleven current ministries. Ordered as the state lists them.
MINISTRIES: tuple[ReferenceOrganisation, ...] = (
    ReferenceOrganisation("Haridus- ja Teadusministeerium", OrganisationType.MINISTRY, ("HTM",)),
    ReferenceOrganisation("Justiits- ja Digiministeerium", OrganisationType.MINISTRY, ("JuDo",)),
    ReferenceOrganisation("Kaitseministeerium", OrganisationType.MINISTRY, ("KaM",)),
    ReferenceOrganisation("Kliimaministeerium", OrganisationType.MINISTRY, ("KliM",)),
    ReferenceOrganisation("Kultuuriministeerium", OrganisationType.MINISTRY, ("KuM",)),
    ReferenceOrganisation(
        "Majandus- ja Kommunikatsiooniministeerium", OrganisationType.MINISTRY, ("MKM",)
    ),
    ReferenceOrganisation("Rahandusministeerium", OrganisationType.MINISTRY, ("RM",)),
    ReferenceOrganisation(
        "Regionaal- ja Põllumajandusministeerium", OrganisationType.MINISTRY, ("REM",)
    ),
    ReferenceOrganisation("Siseministeerium", OrganisationType.MINISTRY, ("SiM",)),
    ReferenceOrganisation("Sotsiaalministeerium", OrganisationType.MINISTRY, ("SoM",)),
    ReferenceOrganisation("Välisministeerium", OrganisationType.MINISTRY, ("VäM",)),
)
