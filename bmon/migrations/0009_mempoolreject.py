# Generated by Django 4.1.2 on 2022-10-25 01:53

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bmon', '0008_alter_peer_servicesnames'),
    ]

    operations = [
        migrations.CreateModel(
            name='MempoolReject',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('host', models.CharField(max_length=200)),
                ('timestamp', models.DateTimeField()),
                ('txhash', models.CharField(max_length=80)),
                ('peer_num', models.IntegerField()),
                ('reason', models.CharField(max_length=1024)),
                ('reason_data', models.JSONField(blank=True, default=dict)),
                ('peer', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='bmon.peer')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
