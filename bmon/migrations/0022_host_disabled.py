# Generated by Django 4.1.7 on 2023-04-12 15:44

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bmon', '0021_alter_host_bitcoin_listen'),
    ]

    operations = [
        migrations.AddField(
            model_name='host',
            name='disabled',
            field=models.BooleanField(default=False),
        ),
    ]
