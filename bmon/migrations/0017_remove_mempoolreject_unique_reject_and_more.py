# Generated by Django 4.1.2 on 2022-10-26 19:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bmon', '0016_remove_mempoolreject_unique_reject_and_more'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='mempoolreject',
            name='unique_reject',
        ),
        migrations.RemoveConstraint(
            model_name='peer',
            name='unique_peer',
        ),
        migrations.RenameField(
            model_name='blockconnectedevent',
            old_name='hostobj',
            new_name='host',
        ),
        migrations.RenameField(
            model_name='blockdisconnectedevent',
            old_name='hostobj',
            new_name='host',
        ),
        migrations.RenameField(
            model_name='connectblockdetails',
            old_name='hostobj',
            new_name='host',
        ),
        migrations.RenameField(
            model_name='connectblockevent',
            old_name='hostobj',
            new_name='host',
        ),
        migrations.RenameField(
            model_name='mempoolreject',
            old_name='hostobj',
            new_name='host',
        ),
        migrations.RenameField(
            model_name='peer',
            old_name='hostobj',
            new_name='host',
        ),
        migrations.RenameField(
            model_name='reorgevent',
            old_name='hostobj',
            new_name='host',
        ),
        migrations.RenameField(
            model_name='requestblockevent',
            old_name='hostobj',
            new_name='host',
        ),
        migrations.AddConstraint(
            model_name='mempoolreject',
            constraint=models.UniqueConstraint(fields=('host', 'timestamp', 'txhash', 'peer_num'), name='unique_reject'),
        ),
        migrations.AddConstraint(
            model_name='peer',
            constraint=models.UniqueConstraint(fields=('host', 'num', 'addr', 'connection_type', 'inbound', 'network', 'services', 'subver', 'version', 'relaytxes', 'bip152_hb_from', 'bip152_hb_to'), name='unique_peer'),
        ),
    ]
