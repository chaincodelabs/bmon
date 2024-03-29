# Generated by Django 4.1.1 on 2022-10-03 16:21

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='ConnectBlockDetails',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('host', models.CharField(max_length=200)),
                ('timestamp', models.DateTimeField()),
                ('blockhash', models.CharField(max_length=80)),
                ('height', models.IntegerField()),
                ('load_block_from_disk_time_ms', models.FloatField()),
                ('sanity_checks_time_ms', models.FloatField()),
                ('fork_checks_time_ms', models.FloatField()),
                ('txin_count', models.IntegerField()),
                ('tx_count', models.IntegerField()),
                ('connect_txs_time_ms', models.FloatField()),
                ('verify_time_ms', models.FloatField()),
                ('index_writing_time_ms', models.FloatField()),
                ('connect_total_time_ms', models.FloatField()),
                ('flush_coins_time_ms', models.FloatField()),
                ('flush_chainstate_time_ms', models.FloatField()),
                ('connect_postprocess_time_ms', models.FloatField()),
                ('connectblock_total_time_ms', models.FloatField()),
            ],
        ),
        migrations.CreateModel(
            name='ConnectBlockEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('host', models.CharField(max_length=200)),
                ('timestamp', models.DateTimeField()),
                ('blockhash', models.CharField(max_length=80)),
                ('height', models.IntegerField()),
                ('log2_work', models.FloatField()),
                ('total_tx_count', models.IntegerField()),
                ('version', models.CharField(max_length=200, null=True)),
                ('date', models.DateTimeField()),
                ('cachesize_mib', models.FloatField(null=True)),
                ('cachesize_txo', models.IntegerField()),
                ('warning', models.CharField(max_length=1024, null=True)),
            ],
        ),
    ]
