"""GraphQL object types for the Approval Engine. Type names are prefixed ``Approval*`` because
graphene type names are global."""
import graphene
from graphene_django import DjangoObjectType

from core import ExtendedConnection
from approval.models import (
    ApprovalFlow, ApprovalRequest, ApprovalStep, ApprovalDecision,
)


class ApprovalFlowGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')

    class Meta:
        model = ApprovalFlow
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "code": ["exact", "icontains", "istartswith"],
            "domain": ["exact", "icontains"],
            "is_active": ["exact"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection


class ApprovalDecisionGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')

    class Meta:
        model = ApprovalDecision
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "step_id": ["exact"],
            "decision": ["exact", "in"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection


class ApprovalStepGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')
    decisions = graphene.List(ApprovalDecisionGQLType)

    class Meta:
        model = ApprovalStep
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "approval_request_id": ["exact"],
            "order": ["exact"],
            "code": ["exact"],
            "status": ["exact", "in"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_decisions(self, info):
        return self.decisions.filter(is_deleted=False).order_by('date_created')


class ApprovalRequestGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')
    steps = graphene.List(ApprovalStepGQLType)
    entity_model = graphene.String()

    class Meta:
        model = ApprovalRequest
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "flow_id": ["exact"],
            "object_id": ["exact"],
            "status": ["exact", "in"],
            "current_step_order": ["exact"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_steps(self, info):
        return self.steps.filter(is_deleted=False).order_by('order')

    def resolve_entity_model(self, info):
        return f'{self.content_type.app_label}.{self.content_type.model}' if self.content_type_id else None
