"""Matter gains Õigusakt: the canonical relation and the free text beside it.

Schema only, and additive only. Every existing Matter stays valid: the relation
starts empty on all of them and blank is a legitimate answer — only ``Pealkiri``
is ever required.

**No business data is populated here.** The historical ``ÕIGUSAKT`` column has a
reviewed reading (``app.taxonomy.legal_instruments``) and it is deliberately not
applied: whether a source value may overwrite what a person chose is a
precedence question nobody has answered, and answering it as a side effect of a
schema migration would be the wrong place to answer it (task §12,
docs/adr/0070).

``Matter.legal_instrument_other`` is *not* a rename of
``CurrentRegisterState.legal_instrument_raw`` and does not replace it. That
column is what the spreadsheet said; this one is what a person typed beside a
``Muu`` chip. Both may exist for one record (task §21).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0014_intake_staging'),
        ('taxonomy', '0005_legal_instrument_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='matter',
            name='legal_instrument_other',
            field=models.CharField(blank=True, help_text='Vabatekst, kui ükski loetletud õigusakt ei sobi. Ei ole taksonoomia: siit ei teki uut õigusakti liiki.', max_length=400, verbose_name='õigusakti liik'),
        ),
        migrations.AddField(
            model_name='matter',
            name='legal_instruments',
            field=models.ManyToManyField(blank=True, related_name='matters', to='taxonomy.legalinstrumenttype', verbose_name='õigusakt'),
        ),
    ]
