# Generated by Django 4.1.2 on 2022-10-29 21:56

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bmon', '0018_alter_host_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='PeerStats',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('num_peers', models.IntegerField()),
                ('ping_mean', models.FloatField()),
                ('ping_min', models.FloatField()),
                ('ping_max', models.FloatField()),
                ('bytesrecv', models.FloatField()),
                ('bytessent', models.FloatField()),
                ('bytesrecv_per_msg', models.JSONField()),
                ('bytessent_per_msg', models.JSONField()),
                ('host', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='bmon.host')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
