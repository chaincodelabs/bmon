# Generated by Django 4.1.2 on 2022-10-22 16:01

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bmon', '0004_connectblockdetails_created_at_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='BlockConnectedEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('host', models.CharField(max_length=200)),
                ('timestamp', models.DateTimeField()),
                ('blockhash', models.CharField(max_length=80)),
                ('height', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='BlockDisconnectedEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('host', models.CharField(max_length=200)),
                ('timestamp', models.DateTimeField()),
                ('blockhash', models.CharField(max_length=80)),
                ('height', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='Peer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('host', models.CharField(max_length=200)),
                ('addr', models.CharField(max_length=256)),
                ('connection_type', models.CharField(max_length=256)),
                ('num', models.IntegerField()),
                ('inbound', models.BooleanField()),
                ('network', models.CharField(max_length=256)),
                ('services', models.CharField(max_length=256)),
                ('servicesnames', models.JSONField()),
                ('subver', models.CharField(max_length=256)),
                ('version', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='ReorgEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('finished_timestamp', models.DateTimeField()),
                ('host', models.CharField(max_length=200)),
                ('min_height', models.IntegerField()),
                ('max_height', models.IntegerField()),
                ('old_blockhashes', models.JSONField()),
                ('new_blockhashes', models.JSONField()),
            ],
        ),
        migrations.CreateModel(
            name='RequestBlockEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('host', models.CharField(max_length=200)),
                ('timestamp', models.DateTimeField()),
                ('blockhash', models.CharField(max_length=80)),
                ('height', models.IntegerField(blank=True, null=True)),
                ('peer_num', models.IntegerField()),
                ('method', models.CharField(max_length=256)),
                ('peer', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='bmon.peer')),
            ],
        ),
    ]
