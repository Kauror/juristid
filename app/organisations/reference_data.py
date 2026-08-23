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


#: The handful of non-ministry bodies legal-policy work cannot be described
#: without. Deliberately tiny.
#:
#: A complete Estonian public-sector directory — boards, inspectorates,
#: agencies, municipalities, associations — is a different project with a
#: different evidence requirement, and seeding hundreds of rows nobody has
#: checked would make the register look authoritative while being unverified.
#: `reference_data coverage` exists to say which further bodies the register
#: actually needs; that measurement comes first.
#:
#: On the missing abbreviations: ministries carry theirs because `MKM` and `RM`
#: are the state's own unambiguous short forms. `EK` is not in that class — it
#: is used in Estonian for Euroopa Komisjon, Euroopa Kohus and Euroopa
#: Kontrollikoda alike, and an alias is an identity decision that matching
#: trusts absolutely. One wrong `EK` would file a Commission consultation under
#: the Court. So Euroopa Komisjon is seeded with no abbreviation, and a reviewed
#: alias can be added later against real register evidence.
CORE_PUBLIC_INSTITUTIONS: tuple[ReferenceOrganisation, ...] = (
    ReferenceOrganisation("Riigikogu", OrganisationType.PARLIAMENT),
    # `Valitsus` is the everyday short form and, unlike `EK`, has no competing
    # referent: nothing else in this domain is called that on its own.
    ReferenceOrganisation("Vabariigi Valitsus", OrganisationType.GOVERNMENT, ("Valitsus",)),
    ReferenceOrganisation("Euroopa Komisjon", OrganisationType.EU_INSTITUTION),
    ReferenceOrganisation("Euroopa Parlament", OrganisationType.EU_INSTITUTION, ("EP",)),
)

#: The whole reviewed public baseline, in the order an operator should read it.
#: This — not `MINISTRIES` alone — is what `reference_data` plans, applies and
#: verifies, and what deployment readiness requires before a real-data
#: environment is called ready.
PUBLIC_REFERENCE_ORGANISATIONS: tuple[ReferenceOrganisation, ...] = (
    *MINISTRIES,
    *CORE_PUBLIC_INSTITUTIONS,
)

#: Bumped when the reviewed public set changes. Part of the plan digest, so a
#: digest approved under one manifest cannot be applied under another.
REFERENCE_ORGANISATION_VERSION = "1.0"

#: Where the ministry names were checked, and when.
ORGANISATION_SOURCE_TITLE = "Ministeeriumid"
ORGANISATION_SOURCE_PUBLISHER = "Eesti Vabariigi Valitsus"
ORGANISATION_SOURCE_URL = "https://valitsus.ee/"
ORGANISATION_SOURCE_VERIFIED_ON = "2026-08-23"
