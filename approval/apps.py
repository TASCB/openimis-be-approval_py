"""AppConfig for the generic Approval Engine.

Loads rights + behaviour from ``core.ModuleConfiguration`` and performs idempotent
``post_migrate`` seeding of the engine's own rights and the default approval flows — mirroring the
``communications`` / ``training`` modules. Module number = ``24`` (rights ``24xxxx``).

The engine's rights (``24xxxx``) cover only its OWN operations (flow admin, request view, cancel /
return / override). A step's decision authorization uses that step's ``required_right``, which is a
DOMAIN right (e.g. ``230201`` access_request manager, ``270102`` payment approve) — never a 24xxxx
right.
"""
import logging
import uuid

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)

MODULE_NAME = 'approval'
IMIS_ADMINISTRATOR_SYSTEM = 64

# Default flows seeded from code. Step ``required_right`` values are DOMAIN rights.
DEFAULT_FLOWS = [
    {
        'code': 'ACCESS_REQUEST_ACCOUNT', 'name': 'Access request — account provisioning',
        'domain': 'access_request.AccessRequest',
        # MANAGER is routed to the applicant's section sponsor; without a sponsor it is right-only.
        'enforce_assigned_role': True,
        'steps': [
            {'code': 'MANAGER', 'label': 'Department / Line Manager', 'required_right': '230201'},
            {'code': 'ICT', 'label': 'ICT Department', 'required_right': '230202'},
        ],
    },
    {
        'code': 'PAYMENT_APPROVAL', 'name': 'Payment / paylist approval',
        'domain': 'tasaf_payment.Paylist',
        # Two-level sign-off. Both steps require the paylist-approve right (270303); the engine's
        # enforce_distinct_approvers guarantees the two signatures come from DIFFERENT people
        # (segregation of duties) without needing two separate rights.
        'enforce_distinct_approvers': True,
        'steps': [
            {'code': 'FINANCE_DIRECTOR', 'label': 'Director of Finance', 'required_right': '270303',
             'assigned_role': 'Payment Approver'},
            {'code': 'EXECUTIVE_DIRECTOR', 'label': 'Executive Director', 'required_right': '270303',
             'assigned_role': 'Payment Approver'},
        ],
    },
    {
        'code': 'MUSE_SETTINGS_CHANGE', 'name': 'MUSE settings / FSP routing change',
        'domain': 'tasaf_payment.MuseChangeRequest',
        'enforce_requester_not_approver': True,
        'steps': [
            {'code': 'MUSE_CHECKER', 'label': 'Payment settings approver', 'required_right': '270903',
             'assigned_role': 'Payment Approver'},
        ],
    },
    {
        'code': 'TRAINING_APPROVAL', 'name': 'Training approval',
        'domain': 'training.Training',
        'steps': [
            {'code': 'SUPERVISOR', 'label': 'Supervisor', 'required_right': '210110',
             'assigned_role': 'PCT Manager'},
        ],
    },
    {
        # A material payment-detail change needs a second person before the account is re-verified.
        'code': 'CASE_PAYMENT_CHANGE', 'name': 'Case management — payment detail change',
        'domain': 'tasaf_payment.PaymentAccount',
        'enforce_distinct_approvers': True,
        'steps': [
            {'code': 'CASE_APPROVER', 'label': 'Payment change approver',
             'required_right': '290502', 'assigned_role': 'Payment Approver'},
        ],
    },
    {
        # Beneficiary enrolment approval. Same pattern as PAYMENT_APPROVAL: a single domain right
        # (enrolment authorisation 170003) with enforce_distinct_approvers separating the signatures
        # by PERSON, and per-step assigned_role routing each level to its tier. A fuller
        # ward -> council -> national -> DP chain with tier-exclusive rights is a future refinement
        # (needs a dedicated per-tier enrolment-approve right in social_protection — see the
        # role-seed developer guide §7).
        'code': 'ENROLMENT_APPROVAL', 'name': 'Beneficiary enrolment approval',
        'domain': 'social_protection.Beneficiary',
        'enforce_distinct_approvers': True,
        'steps': [
            {'code': 'COUNCIL', 'label': 'Council endorsement', 'required_right': '170003',
             'assigned_role': 'Council Coordinator'},
            {'code': 'DP', 'label': 'Director of Programs approval', 'required_right': '170003',
             'assigned_role': 'Director of Programs'},
        ],
    },
]

DEFAULT_CONFIG = {
    # Flow configuration (admin)
    'gql_flow_search_perms': ['240101'],
    'gql_flow_create_perms': ['240102'],
    'gql_flow_update_perms': ['240103'],
    'gql_flow_delete_perms': ['240104'],
    # Approval request (view the dashboard/detail)
    'gql_request_search_perms': ['240201'],
    # Engine actions (not per-step approval — that uses the step's domain right)
    'gql_cancel_perms': ['240301'],
    'gql_return_perms': ['240302'],
    'gql_override_perms': ['240303'],
    # Seeding
    'seed_flows': True,
}

ALL_RIGHTS = [
    240101, 240102, 240103, 240104,
    240201,
    240301, 240302, 240303,
]


class ApprovalConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = MODULE_NAME

    gql_flow_search_perms = []
    gql_flow_create_perms = []
    gql_flow_update_perms = []
    gql_flow_delete_perms = []
    gql_request_search_perms = []
    gql_cancel_perms = []
    gql_return_perms = []
    gql_override_perms = []
    seed_flows = True

    def ready(self):
        from core.models import ModuleConfiguration
        cfg = ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CONFIG)
        self.__load_config(cfg)
        post_migrate.connect(on_post_migrate, sender=self)

    @classmethod
    def __load_config(cls, cfg):
        for field in cfg:
            if hasattr(ApprovalConfig, field):
                setattr(ApprovalConfig, field, cfg[field])


def on_post_migrate(sender, **kwargs):
    apps = kwargs.get('apps')
    try:
        _seed_admin_rights(apps)
    except Exception as exc:
        logger.warning("approval: rights seeding skipped (%s)", exc)
    try:
        if ApprovalConfig.seed_flows:
            _seed_flows(apps)
    except Exception as exc:
        logger.warning("approval: flow seeding skipped (%s)", exc)


def _seed_admin_rights(apps):
    Role = apps.get_model('core', 'Role')
    RoleRight = apps.get_model('core', 'RoleRight')
    role = Role.objects.filter(is_system=IMIS_ADMINISTRATOR_SYSTEM, validity_to__isnull=True).first()
    if not role:
        return
    for right_id in ALL_RIGHTS:
        if not RoleRight.objects.filter(role=role, right_id=right_id, validity_to__isnull=True).exists():
            RoleRight.objects.create(role=role, right_id=right_id, audit_user_id=1)


def _flow_config(flow, role_by_name=None):
    """The stored ApprovalFlow.config from a DEFAULT_FLOWS entry (steps + flow-level flags).

    A step may carry ``assigned_role`` (a role NAME) for portability across environments; it is
    resolved to ``assigned_role_id`` at seed time via ``role_by_name`` (DB role ids differ per
    environment, so names — not ids — live in code). Unresolvable names are dropped rather than
    stored, so the flow still works (routing is optional; authorization is the step's required_right).
    """
    role_by_name = role_by_name or {}
    steps = []
    for step in flow['steps']:
        resolved = {k: v for k, v in step.items() if k != 'assigned_role'}
        role_name = step.get('assigned_role')
        if role_name and role_by_name.get(role_name) is not None:
            resolved['assigned_role_id'] = role_by_name[role_name]
        steps.append(resolved)
    cfg = {'steps': steps}
    if flow.get('enforce_distinct_approvers'):
        cfg['enforce_distinct_approvers'] = True
    if flow.get('enforce_requester_not_approver'):
        cfg['enforce_requester_not_approver'] = True
    return cfg


def _seed_flows(apps):
    """Upsert the code-defined flows. Flows are code-managed config: on an existing flow the
    name/domain/config are refreshed from DEFAULT_FLOWS so rights/step changes propagate."""
    ApprovalFlow = apps.get_model('approval', 'ApprovalFlow')
    Role = apps.get_model('core', 'Role')
    User = apps.get_model('core', 'User')
    admin = User.objects.order_by('id').first()
    if not admin:
        return
    # Resolve assigned_role names → ids once (later/higher id wins on duplicate names).
    role_by_name = {
        r.name: r.id
        for r in Role.objects.filter(validity_to__isnull=True).order_by('id')
    }
    for flow in DEFAULT_FLOWS:
        cfg = _flow_config(flow, role_by_name)
        existing = ApprovalFlow.objects.filter(code=flow['code']).first()
        if existing:
            if getattr(existing, 'is_user_managed', False):
                continue  # admin-customised — never overwrite from code
            if (existing.name, existing.domain, existing.config) != (flow['name'], flow['domain'], cfg):
                existing.name, existing.domain, existing.config = flow['name'], flow['domain'], cfg
                existing.user_updated_id = admin.id
                existing.save()
            continue
        ApprovalFlow.objects.create(
            id=uuid.uuid4(), code=flow['code'], name=flow['name'], domain=flow['domain'],
            is_active=True, config=cfg, version=1,
            user_created_id=admin.id, user_updated_id=admin.id)
