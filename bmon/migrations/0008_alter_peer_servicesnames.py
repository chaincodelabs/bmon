# Generated by Django 4.1.2 on 2022-10-24 19:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bmon', '0007_alter_peer_bip152_hb_from_alter_peer_bip152_hb_to'),
    ]

    operations = [
        migrations.AlterField(
            model_name='peer',
            name='servicesnames',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
