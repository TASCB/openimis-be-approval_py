from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('approval', '0003_renumber_domain_rights'),
    ]

    operations = [
        migrations.AddField(
            model_name='approvalstep',
            name='assigned_group_id',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='historicalapprovalstep',
            name='assigned_group_id',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
