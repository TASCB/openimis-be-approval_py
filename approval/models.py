"""Generic approval engine models."""
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import HistoryModel, User, UUIDModel, ObjectMutation, MutationLog


class RequestStatus(models.TextChoices):
    PENDING = 'PENDING', _('Pending')
    APPROVED = 'APPROVED', _('Approved')
    REJECTED = 'REJECTED', _('Rejected')
    CANCELLED = 'CANCELLED', _('Cancelled')
    RETURNED = 'RETURNED', _('Returned for correction')


TERMINAL_REQUEST_STATUSES = (
    RequestStatus.APPROVED, RequestStatus.REJECTED, RequestStatus.CANCELLED,
)


class StepStatus(models.TextChoices):
    PENDING = 'PENDING', _('Pending')
    APPROVED = 'APPROVED', _('Approved')
    REJECTED = 'REJECTED', _('Rejected')
    SKIPPED = 'SKIPPED', _('Skipped')
    RETURNED = 'RETURNED', _('Returned for correction')


class DecisionType(models.TextChoices):
    APPROVED = 'APPROVED', _('Approved')
    REJECTED = 'REJECTED', _('Rejected')
    RETURNED = 'RETURNED', _('Returned for correction')
    CANCELLED = 'CANCELLED', _('Cancelled')


class ApprovalFlow(HistoryModel):
    """A named, ordered approval flow bound to a domain (e.g. ``access_request.AccessRequest``).

    ``config`` holds the ordered step definitions
    (``{"steps": [{"code","label","required_right","assigned_role_id"}]}``). Flows are seeded from
    code via ``post_migrate``; ``config`` lets an admin override without a code change.
    """
    code = models.CharField(max_length=100, blank=False, null=False)
    name = models.CharField(max_length=255, blank=False, null=False)
    domain = models.CharField(max_length=255, blank=False, null=False)
    is_active = models.BooleanField(default=True)
    config = models.JSONField(default=dict, blank=True)
    # Code seeding leaves admin-managed flows untouched.
    is_user_managed = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=['code']), models.Index(fields=['domain']),
                   models.Index(fields=['is_active'])]

    def __str__(self):
        return f'{self.code} ({self.domain})'


class ApprovalRequest(HistoryModel):
    """One approval process for one target entity, progressing through its flow's steps."""
    flow = models.ForeignKey(
        ApprovalFlow, on_delete=models.DO_NOTHING, related_name='requests')
    content_type = models.ForeignKey(
        ContentType, on_delete=models.DO_NOTHING, blank=True, null=True)
    object_id = models.CharField(max_length=255, blank=True, null=True)
    entity = GenericForeignKey('content_type', 'object_id')
    status = models.CharField(
        max_length=30, choices=RequestStatus.choices, default=RequestStatus.PENDING)
    current_step_order = models.PositiveIntegerField(default=1)
    requested_by = models.ForeignKey(
        User, on_delete=models.DO_NOTHING, blank=True, null=True, related_name='+')
    requested_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['flow']),
            models.Index(fields=['status']),
            models.Index(fields=['content_type', 'object_id']),
        ]

    def __str__(self):
        return f'{self.flow_id} / {self.object_id} [{self.status}]'


class ApprovalStep(HistoryModel):
    """A single sequential step within a request.

    ``required_right`` is a DOMAIN right code (string, checked via ``user.has_perms([...])``) — the
    engine owns no per-step authorization rights. ``assigned_role_id`` is optional routing metadata.
    ``task_id`` links a ``tasks_management`` Task used as the approver inbox (Phase 1).
    """
    approval_request = models.ForeignKey(
        ApprovalRequest, on_delete=models.DO_NOTHING, related_name='steps')
    order = models.PositiveIntegerField()
    code = models.CharField(max_length=100, blank=False, null=False)
    label = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(
        max_length=30, choices=StepStatus.choices, default=StepStatus.PENDING)
    required_right = models.CharField(max_length=10, blank=True, null=True)
    assigned_role_id = models.IntegerField(blank=True, null=True)
    task_id = models.UUIDField(blank=True, null=True)

    class Meta:
        ordering = ['order']
        indexes = [models.Index(fields=['approval_request']), models.Index(fields=['status'])]

    def __str__(self):
        return f'{self.approval_request_id} #{self.order} {self.code} [{self.status}]'


class ApprovalDecision(HistoryModel):
    """An approver's recorded decision on a step (the signed, timestamped audit block)."""
    step = models.ForeignKey(
        ApprovalStep, on_delete=models.DO_NOTHING, related_name='decisions')
    approver = models.ForeignKey(
        User, on_delete=models.DO_NOTHING, blank=True, null=True, related_name='+')
    decision = models.CharField(max_length=30, choices=DecisionType.choices)
    comment = models.TextField(blank=True, null=True)
    signature = models.TextField(blank=True, null=True)
    decided_at = models.DateTimeField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=['step']), models.Index(fields=['decision'])]

    def __str__(self):
        return f'{self.step_id} = {self.decision} by {self.approver_id}'


class ApprovalFlowMutation(UUIDModel, ObjectMutation):
    approval_flow = models.ForeignKey(ApprovalFlow, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(MutationLog, models.DO_NOTHING, related_name='approval_flows')


class ApprovalRequestMutation(UUIDModel, ObjectMutation):
    approval_request = models.ForeignKey(ApprovalRequest, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(MutationLog, models.DO_NOTHING, related_name='approval_requests')


class ApprovalStepMutation(UUIDModel, ObjectMutation):
    approval_step = models.ForeignKey(ApprovalStep, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(MutationLog, models.DO_NOTHING, related_name='approval_steps')


class ApprovalDecisionMutation(UUIDModel, ObjectMutation):
    approval_decision = models.ForeignKey(ApprovalDecision, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(MutationLog, models.DO_NOTHING, related_name='approval_decisions')
