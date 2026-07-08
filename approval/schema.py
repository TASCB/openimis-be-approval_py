"""Federated GraphQL schema for the Approval Engine (openIMIS discovers ``Query``/``Mutation``)."""
import graphene
import graphene_django_optimizer as gql_optimizer
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _

from core.schema import OrderedDjangoFilterConnectionField

from approval.apps import ApprovalConfig
from approval.models import ApprovalFlow, ApprovalRequest, ApprovalStep, ApprovalDecision
from approval.gql_queries import (
    ApprovalFlowGQLType, ApprovalRequestGQLType, ApprovalStepGQLType, ApprovalDecisionGQLType,
)
from approval.gql_mutations import (
    ApproveStepMutation, RejectStepMutation, ReturnStepMutation, CancelRequestMutation,
    UpdateApprovalFlowMutation,
)
from approval.services import ApprovalService


def _check(user, perms):
    if type(user) is AnonymousUser or not user.id or not user.has_perms(perms):
        raise PermissionDenied(_("unauthorized"))


class Query(graphene.ObjectType):
    approval_flow = OrderedDjangoFilterConnectionField(
        ApprovalFlowGQLType, orderBy=graphene.List(of_type=graphene.String))
    approval_request = OrderedDjangoFilterConnectionField(
        ApprovalRequestGQLType, orderBy=graphene.List(of_type=graphene.String),
        actionable_by_me=graphene.Boolean())
    approval_step = OrderedDjangoFilterConnectionField(
        ApprovalStepGQLType, orderBy=graphene.List(of_type=graphene.String))
    approval_decision = OrderedDjangoFilterConnectionField(
        ApprovalDecisionGQLType, orderBy=graphene.List(of_type=graphene.String))
    # Everything the current user can currently act on (rights-based), for the "My Approvals" view.
    approval_my_pending = graphene.List(ApprovalRequestGQLType)

    def resolve_approval_flow(self, info, **kwargs):
        _check(info.context.user, ApprovalConfig.gql_flow_search_perms)
        return gql_optimizer.query(ApprovalFlow.objects.filter(is_deleted=False), info)

    def resolve_approval_request(self, info, **kwargs):
        _check(info.context.user, ApprovalConfig.gql_request_search_perms)
        qs = ApprovalRequest.objects.filter(is_deleted=False).order_by('-date_created')
        if kwargs.get('actionable_by_me'):
            user = info.context.user
            ids = [r.id for r in ApprovalService(user).get_pending_for_user(user)]
            qs = qs.filter(id__in=ids)
        return gql_optimizer.query(qs, info)

    def resolve_approval_step(self, info, **kwargs):
        _check(info.context.user, ApprovalConfig.gql_request_search_perms)
        return gql_optimizer.query(ApprovalStep.objects.filter(is_deleted=False), info)

    def resolve_approval_decision(self, info, **kwargs):
        _check(info.context.user, ApprovalConfig.gql_request_search_perms)
        return gql_optimizer.query(ApprovalDecision.objects.filter(is_deleted=False), info)

    def resolve_approval_my_pending(self, info, **kwargs):
        user = info.context.user
        if type(user) is AnonymousUser or not user.id:
            raise PermissionDenied(_("unauthorized"))
        return ApprovalService(user).get_pending_for_user(user)


class Mutation(graphene.ObjectType):
    approve_approval_step = ApproveStepMutation.Field()
    reject_approval_step = RejectStepMutation.Field()
    return_approval_step = ReturnStepMutation.Field()
    cancel_approval_request = CancelRequestMutation.Field()
    update_approval_flow = UpdateApprovalFlowMutation.Field()
