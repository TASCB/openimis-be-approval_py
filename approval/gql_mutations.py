"""GraphQL mutations for the Approval Engine — the approver *actions*.

``request_approval`` is NOT exposed here: domain modules create requests by calling
``ApprovalService.request_approval`` in their own service layer. GraphQL only needs the human
actions (approve / reject / return / cancel). Each mutation is a thin ``BaseMutation`` that defers
to ``ApprovalService`` (which owns validation + rights checks), mirroring the communications
``PublishPostMutation`` pattern.
"""
import json

import graphene
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _

from core.gql.gql_mutations.base_mutation import BaseMutation
from core.schema import OpenIMISMutation

from approval.apps import ApprovalConfig
from approval.services import ApprovalService, ApprovalFlowService


def _strip_client(data):
    data.pop('client_mutation_id', None)
    data.pop('client_mutation_label', None)


class _ApprovalActionLogic:
    """Plain mixin — run one ApprovalService action. Does NOT call abstract super()._validate."""
    _action = None          # ApprovalService method name
    _needs_step = True       # approve/reject/return act on a step; cancel does not

    @classmethod
    def _validate_mutation(cls, user, **data):
        # Authorization is enforced inside ApprovalService (step required_right / override / cancel).
        return None

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        service = ApprovalService(user)
        kwargs = {'request_id': data.get('request_id')}
        if cls._needs_step:
            kwargs['step_id'] = data.get('step_id')
            kwargs['comment'] = data.get('comment')
            if cls._action != 'return_for_correction':
                kwargs['signature'] = data.get('signature')
        else:
            kwargs['reason'] = data.get('reason')
        res = getattr(service, cls._action)(**kwargs)
        return res if not res.get('success') else None


class ApproveStepMutation(_ApprovalActionLogic, BaseMutation):
    _mutation_module = "approval"
    _mutation_class = "ApproveStepMutation"
    _action = 'approve'

    class Input(OpenIMISMutation.Input):
        request_id = graphene.UUID(required=True)
        step_id = graphene.UUID(required=True)
        comment = graphene.String(required=False)
        signature = graphene.String(required=False)


class RejectStepMutation(_ApprovalActionLogic, BaseMutation):
    _mutation_module = "approval"
    _mutation_class = "RejectStepMutation"
    _action = 'reject'

    class Input(OpenIMISMutation.Input):
        request_id = graphene.UUID(required=True)
        step_id = graphene.UUID(required=True)
        comment = graphene.String(required=False)
        signature = graphene.String(required=False)


class ReturnStepMutation(_ApprovalActionLogic, BaseMutation):
    _mutation_module = "approval"
    _mutation_class = "ReturnStepMutation"
    _action = 'return_for_correction'

    class Input(OpenIMISMutation.Input):
        request_id = graphene.UUID(required=True)
        step_id = graphene.UUID(required=True)
        comment = graphene.String(required=False)


class CancelRequestMutation(_ApprovalActionLogic, BaseMutation):
    _mutation_module = "approval"
    _mutation_class = "CancelRequestMutation"
    _action = 'cancel'
    _needs_step = False

    class Input(OpenIMISMutation.Input):
        request_id = graphene.UUID(required=True)
        reason = graphene.String(required=False)


# Admin: edit an EXISTING flow's steps/config (update-only; code/domain are read-only contracts).
class UpdateApprovalFlowMutation(BaseMutation):
    _mutation_module = "approval"
    _mutation_class = "UpdateApprovalFlowMutation"

    @classmethod
    def _validate_mutation(cls, user, **data):
        if not user.has_perms(ApprovalConfig.gql_flow_update_perms):
            raise PermissionDenied(_("unauthorized"))

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        config = None
        if data.get('config') is not None:
            try:
                config = json.loads(data['config'])
            except Exception:
                return {"success": False, "message": _("approval.flow.bad_config_json")}
        res = ApprovalFlowService(user).update(
            data.get('id'), config=config, is_active=data.get('is_active'))
        return res if not res.get('success') else None

    class Input(OpenIMISMutation.Input):
        id = graphene.UUID(required=True)
        config = graphene.String(required=False)   # JSON: {"steps":[...],"enforce_distinct_approvers":bool}
        is_active = graphene.Boolean(required=False)
