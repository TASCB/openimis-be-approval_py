"""Follow the legacy_individual (26xxxx) and tasaf_payment (270xxx) renumbering.

A step's domain right is stored on ApprovalFlow.config and snapshotted on ApprovalStep;
code-managed flows resync from DEFAULT_FLOWS at startup, user-managed flows and in-flight
steps do not. History tables keep the old codes on purpose.
"""
from django.db import migrations

RIGHT_MAP = {
    '152001': '270001', '152002': '270002', '152003': '270003', '152004': '270004',
    '152101': '270101', '152102': '270102', '152103': '270103',
    '152201': '270201',
    '152301': '270301', '152302': '270302', '152303': '270303', '152304': '270304',
    '152401': '270401', '152501': '270501', '152601': '270601',
    '200001': '260001', '200002': '260002', '200003': '260003', '200004': '260004',
    '200011': '260011', '200012': '260012', '200013': '260013', '200014': '260014',
    '200021': '260021', '200031': '260031', '200041': '260041',
}


def _remap(apps, mapping):
    ApprovalFlow = apps.get_model('approval', 'ApprovalFlow')
    ApprovalStep = apps.get_model('approval', 'ApprovalStep')

    for flow in ApprovalFlow.objects.all():
        config = flow.config or {}
        changed = False
        for step in config.get('steps') or []:
            new = mapping.get(str(step.get('required_right')))
            if new:
                step['required_right'] = new
                changed = True
        if changed:
            flow.config = config
            flow.save(update_fields=['config'])

    for old, new in mapping.items():
        ApprovalStep.objects.filter(required_right=old).update(required_right=new)


def renumber(apps, schema_editor):
    _remap(apps, RIGHT_MAP)


def restore(apps, schema_editor):
    _remap(apps, {new: old for old, new in RIGHT_MAP.items()})


class Migration(migrations.Migration):
    dependencies = [
        ('approval', '0002_approvalflow_is_user_managed_and_more'),
        ('legacy_individual', '0005_renumber_rights_to_26xxxx'),
        ('tasaf_payment', '0004_renumber_rights_to_27xxxx'),
    ]

    operations = [
        migrations.RunPython(renumber, restore),
    ]
