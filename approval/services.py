"""Service layer for the generic Approval Engine.

``ApprovalService`` is a plain orchestration service (custom sequential transitions wrapped in
``transaction.atomic``), patterned on ``access_request``'s approval service — NOT a
``BaseService`` CRUD wrapper. It returns plain ``{success, message, data}`` dicts (no
``model_representation``/JSON round-trip), so file/complex fields are never a problem.

Authorization for a step decision uses that step's ``required_right`` (a DOMAIN right), or the
engine override right. On the final decision the service fires ``approval_service.finalized`` — the
hook domain modules bind their adapters to (see ``signals``). The engine never imports domain code.
"""
import logging

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import User
from core.signals import register_service_signal

from approval.apps import ApprovalConfig
from approval.models import (
    ApprovalFlow, ApprovalRequest, ApprovalStep, ApprovalDecision,
    RequestStatus, StepStatus, DecisionType, TERMINAL_REQUEST_STATUSES,
)

logger = logging.getLogger(__name__)


def _ok(message, data=None):
    return {"success": True, "message": message, "data": data or {}}


def _fail(message, detail=None):
    return {"success": False, "message": message, "detail": detail}


class ApprovalFlowService:
    """Admin editing of an EXISTING flow's steps/config. Update-only — creating flows and changing
    ``code``/``domain`` are code contracts (a flow needs a domain's ``request_approval`` call + a
    finalize adapter, which are code), so they are intentionally not editable here."""

    def __init__(self, user):
        self.user = user

    def update(self, flow_id, config=None, is_active=None):
        try:
            with transaction.atomic():
                flow = ApprovalFlow.objects.filter(id=flow_id, is_deleted=False).first()
                if not flow:
                    return _fail(_("approval.flow_not_found"), str(flow_id))
                if config is not None:
                    err = self._validate_config(config)
                    if err:
                        return _fail(err)
                    flow.config = config
                if is_active is not None:
                    flow.is_active = bool(is_active)
                flow.is_user_managed = True  # from now on, code seeding won't overwrite it
                flow.save(username=self.user.username)
                return _ok(_("approval.flow_updated"), {"id": str(flow.id)})
        except Exception as exc:
            logger.error("approval.flow update failed", exc_info=exc)
            return _fail(_("approval.flow_update_failed"), str(exc))

    @staticmethod
    def _validate_config(config):
        steps = (config or {}).get('steps') or []
        if not steps:
            return _("approval.flow.no_steps")
        codes = [str(s.get('code') or '').strip() for s in steps]
        if any(not c for c in codes):
            return _("approval.flow.step_code_required")
        if len(set(codes)) != len(codes):
            return _("approval.flow.step_codes_unique")
        from core.models import RoleRight
        for s in steps:
            rr = s.get('required_right')
            if rr in (None, ''):
                continue  # a step with no right is open to any authenticated approver
            if not str(rr).isdigit():
                return _("approval.flow.right_not_numeric") + f" ({rr})"
            # a right granted to no role means nobody could ever approve this step
            if not RoleRight.objects.filter(right_id=int(rr), validity_to__isnull=True).exists():
                return _("approval.flow.right_unassigned") + f" ({rr})"
        return None


class ApprovalService:
    def __init__(self, user):
        self.user = user

    # ---- request lifecycle -------------------------------------------------
    @register_service_signal('approval_service.request_approval')
    def request_approval(self, entity, flow_code, summary=None, step_roles=None, step_groups=None):
        """Create an ApprovalRequest for ``entity`` from the flow ``flow_code`` and its steps.

        ``step_roles`` maps a step code to a core.Role id, overriding the flow's
        ``assigned_role`` for this request only. ``step_groups`` maps a step code to an
        auth.Group id whose members alone may sign that step.
        """
        try:
            with transaction.atomic():
                flow = ApprovalFlow.objects.filter(
                    code=flow_code, is_active=True, is_deleted=False).first()
                if not flow:
                    return _fail(_("approval.flow_not_found"), flow_code)
                steps_cfg = (flow.config or {}).get('steps') or []
                if not steps_cfg:
                    return _fail(_("approval.flow_has_no_steps"), flow_code)

                ct = ContentType.objects.get_for_model(entity.__class__)
                req = ApprovalRequest(
                    flow=flow, content_type=ct, object_id=str(entity.pk),
                    status=RequestStatus.PENDING, current_step_order=1,
                    requested_by=self._user_obj(), requested_at=timezone.now(),
                    summary=summary or {})
                req.save(username=self.user.username)

                overrides = step_roles or {}
                groups = step_groups or {}
                for i, s in enumerate(steps_cfg, start=1):
                    code = s.get('code') or f'STEP_{i}'
                    ApprovalStep(
                        approval_request=req, order=i, code=code,
                        label=s.get('label'), status=StepStatus.PENDING,
                        required_right=str(s['required_right']) if s.get('required_right') else None,
                        assigned_role_id=overrides.get(code) or s.get('assigned_role_id'),
                        assigned_group_id=groups.get(code),
                    ).save(username=self.user.username)

                self.sync_task_for_current_step(req)
                return _ok(_("approval.request_created"), {"id": str(req.id)})
        except Exception as exc:
            logger.error("approval.request_approval failed", exc_info=exc)
            return _fail(_("approval.request_failed"), str(exc))

    @register_service_signal('approval_service.approve')
    def approve(self, request_id, step_id, comment=None, signature=None):
        return self._decide(request_id, step_id, DecisionType.APPROVED, comment, signature)

    @register_service_signal('approval_service.reject')
    def reject(self, request_id, step_id, comment=None, signature=None):
        return self._decide(request_id, step_id, DecisionType.REJECTED, comment, signature)

    @register_service_signal('approval_service.return_for_correction')
    def return_for_correction(self, request_id, step_id, comment=None):
        return self._decide(request_id, step_id, DecisionType.RETURNED, comment, None)

    @register_service_signal('approval_service.cancel')
    def cancel(self, request_id, reason=None):
        """Cancel a whole request. Allowed to the requester or a holder of the cancel/override right."""
        try:
            with transaction.atomic():
                req = self._get_request(request_id)
                if not req:
                    return _fail(_("approval.request_not_found"), request_id)
                if req.status in TERMINAL_REQUEST_STATUSES:
                    return _fail(_("approval.already_final"), req.status)
                is_requester = req.requested_by_id and req.requested_by_id == getattr(self.user, 'id', None)
                if not (is_requester
                        or self.user.has_perms(ApprovalConfig.gql_cancel_perms)
                        or self.user.has_perms(ApprovalConfig.gql_override_perms)):
                    return _fail(_("approval.unauthorized"))
                req.status = RequestStatus.CANCELLED
                req.completed_at = timezone.now()
                if reason:
                    req.summary = {**(req.summary or {}), 'cancel_reason': reason}
                req.save(username=self.user.username)
                current = req.steps.filter(order=req.current_step_order, is_deleted=False).first()
                self._close_task(current, failed=True)
                self._finalize(req, DecisionType.CANCELLED)
                return _ok(_("approval.cancelled"), {"id": str(req.id)})
        except Exception as exc:
            logger.error("approval.cancel failed", exc_info=exc)
            return _fail(_("approval.cancel_failed"), str(exc))

    # ---- core decision transition -----------------------------------------
    def _decide(self, request_id, step_id, decision, comment, signature):
        try:
            with transaction.atomic():
                req = self._get_request(request_id)
                if not req:
                    return _fail(_("approval.request_not_found"), request_id)
                if req.status != RequestStatus.PENDING:
                    return _fail(_("approval.not_pending"), req.status)
                step = req.steps.filter(id=step_id, is_deleted=False).first()
                if not step:
                    return _fail(_("approval.step_not_found"), step_id)
                if step.order != req.current_step_order or step.status != StepStatus.PENDING:
                    return _fail(_("approval.not_current_step"), str(step.order))

                rr = step.required_right
                override = self.user.has_perms(ApprovalConfig.gql_override_perms)
                if rr and not (self.user.has_perms([rr]) or override):
                    return _fail(_("approval.unauthorized"))

                # enforce_assigned_role: an assigned step is that role's to sign, not any right holder's.
                if (step.assigned_role_id and not override
                        and (req.flow.config or {}).get('enforce_assigned_role')):
                    me = self._user_obj()
                    if not (me and self._holds_role(me, step.assigned_role_id)):
                        return _fail(_("approval.unauthorized"))

                if step.assigned_group_id and not override:
                    me = self._user_obj()
                    if not (me and self._in_group(me, step.assigned_group_id)):
                        return _fail(_("approval.unauthorized"))

                if (decision == DecisionType.APPROVED
                        and (req.flow.config or {}).get('enforce_requester_not_approver')
                        and req.requested_by_id and req.requested_by_id == getattr(self._user_obj(), 'id', None)):
                    return _fail(_("approval.requester_cannot_approve"))

                # Segregation of duties: on a flow that requires distinct approvers, the same person
                # may not sign more than one step of the same request.
                if decision == DecisionType.APPROVED and (req.flow.config or {}).get('enforce_distinct_approvers'):
                    me = self._user_obj()
                    if me and ApprovalDecision.objects.filter(
                            step__approval_request=req, approver=me,
                            decision=DecisionType.APPROVED, is_deleted=False).exists():
                        return _fail(_("approval.distinct_approver_required"))

                ApprovalDecision(
                    step=step, approver=self._user_obj(), decision=decision,
                    comment=comment, signature=signature, decided_at=timezone.now(),
                ).save(username=self.user.username)

                if decision == DecisionType.APPROVED:
                    step.status = StepStatus.APPROVED
                    step.save(username=self.user.username)
                    self._close_task(step)
                    next_step = req.steps.filter(order=step.order + 1, is_deleted=False).first()
                    if next_step:
                        req.current_step_order = next_step.order
                        req.save(username=self.user.username)
                        self.sync_task_for_current_step(req)
                        return _ok(_("approval.step_approved"),
                                   {"id": str(req.id), "status": req.status, "current_step_order": req.current_step_order})
                    req.status = RequestStatus.APPROVED
                    req.completed_at = timezone.now()
                    req.save(username=self.user.username)
                    self._finalize(req, DecisionType.APPROVED)
                    return _ok(_("approval.request_approved"), {"id": str(req.id), "status": req.status})

                if decision == DecisionType.REJECTED:
                    step.status = StepStatus.REJECTED
                    step.save(username=self.user.username)
                    self._close_task(step, failed=True)
                    req.status = RequestStatus.REJECTED
                    req.completed_at = timezone.now()
                    req.save(username=self.user.username)
                    self._finalize(req, DecisionType.REJECTED)
                    return _ok(_("approval.request_rejected"), {"id": str(req.id), "status": req.status})

                # RETURNED — not terminal; the domain corrects and re-submits.
                step.status = StepStatus.RETURNED
                step.save(username=self.user.username)
                self._close_task(step)
                req.status = RequestStatus.RETURNED
                req.save(username=self.user.username)
                return _ok(_("approval.request_returned"), {"id": str(req.id), "status": req.status})
        except Exception as exc:
            logger.error("approval._decide failed", exc_info=exc)
            return _fail(_("approval.decision_failed"), str(exc))

    # ---- reads -------------------------------------------------------------
    def get_pending_for_user(self, user=None):
        """PENDING requests whose current step is actionable by ``user`` (rights-based)."""
        user = user or self.user
        out = []
        for req in ApprovalRequest.objects.filter(status=RequestStatus.PENDING, is_deleted=False):
            step = req.steps.filter(order=req.current_step_order, is_deleted=False).first()
            if not step:
                continue
            if step.required_right and not user.has_perms([step.required_right]):
                continue
            if step.assigned_group_id and not self._in_group(user, step.assigned_group_id):
                continue
            out.append(req)
        return out

    # ---- tasks_management inbox integration -------------------------------
    @staticmethod
    def _holds_role(user, role_id):
        """Does this core.User hold ``role_id``?"""
        from core.models import UserRole
        i_user_id = getattr(user, 'i_user_id', None)
        if not i_user_id:
            return False
        return UserRole.objects.filter(
            user_id=i_user_id, role_id=role_id, validity_to__isnull=True).exists()

    @staticmethod
    def _in_group(user, group_id):
        return bool(getattr(user, 'id', None)) and user.groups.filter(id=group_id).exists()

    def resolve_step_approvers(self, step):
        """core.User ids who may act on ``step``: holders of ``required_right``, narrowed to
        ``assigned_role`` when the step carries one.

        Path: ``RoleRight(right_id) -> Role -> UserRole.user (InteractiveUser) -> core.User.i_user``.
        """
        if not step or not step.required_right:
            return []
        try:
            right = int(step.required_right)
        except (TypeError, ValueError):
            return []
        from core.models import RoleRight, UserRole
        role_ids = list(RoleRight.objects.filter(
            right_id=right, validity_to__isnull=True).values_list('role_id', flat=True).distinct())
        if not role_ids:
            return []
        flow_cfg = getattr(getattr(step.approval_request, 'flow', None), 'config', None) or {}
        if step.assigned_role_id and flow_cfg.get('enforce_assigned_role'):
            role_ids = [r for r in role_ids if r == step.assigned_role_id]
            if not role_ids:
                return []
        i_user_ids = list(UserRole.objects.filter(
            role_id__in=role_ids, validity_to__isnull=True).values_list('user_id', flat=True).distinct())
        if not i_user_ids:
            return []
        users = User.objects.filter(i_user_id__in=i_user_ids, validity_to__isnull=True)
        if step.assigned_group_id:
            users = users.filter(groups__id=step.assigned_group_id)
        return list(users.values_list('id', flat=True))

    def sync_task_for_current_step(self, approval_request):
        """Best-effort ``tasks_management`` inbox Task for the current step.

        Resolves approvers -> a ``TaskGroup(ANY)`` sourced ``approval:<step>`` -> a ``Task`` (which
        auto-joins that group by ``source``) carrying business_event ``approval.<flow>.<step>`` and
        the ids the reverse handler needs. Never breaks the approval flow — wrapped in try/except and
        run as a savepoint, exactly like access_request's best-effort ``_emit_task``.
        """
        step = approval_request.steps.filter(
            order=approval_request.current_step_order, is_deleted=False).first()
        if not step or step.task_id:
            return None
        approver_ids = self.resolve_step_approvers(step)
        if not approver_ids:
            logger.info("approval: no approvers resolved for step %s (right %s)",
                        step.id, step.required_right)
            return None
        try:
            from tasks_management.models import TaskGroup
            from tasks_management.services import TaskService, TaskGroupService
            source = f'approval:{step.id}'
            if not TaskGroup.objects.filter(json_ext__contains={"task_sources": [source]}).exists():
                grp = TaskGroupService(self.user).create({
                    'code': f'APPROVAL-{step.code}-{str(step.id)[:8]}',
                    'completion_policy': 'ANY',
                    'user_ids': [str(u) for u in approver_ids],
                    'task_sources': [source],
                })
                if not grp.get('success'):
                    logger.warning("approval: task group create failed: %s", grp)
                    return None
            ct = ContentType.objects.get_for_model(ApprovalRequest)
            res = TaskService(self.user).create({
                'source': source,
                'entity_type': ct,
                'entity_id': str(approval_request.id),
                'business_event': f'approval.{approval_request.flow.code}.{step.code}',
                'executor_action_event': 'APPROVAL_DECISION',
                'data': {
                    'approval_request_id': str(approval_request.id),
                    'approval_step_id': str(step.id),
                    'flow_code': approval_request.flow.code,
                    'step_code': step.code,
                    'summary': approval_request.summary or {},
                },
            })
            if res.get('success'):
                step.task_id = res['data']['id']
                step.save(username=self.user.username)
            return res
        except Exception as exc:
            logger.warning("approval: sync_task_for_current_step failed (%s)", exc)
            return None

    def _close_task(self, step, failed=False):
        """Mark a step's inbox Task done via a DIRECT model save (no ``complete_task`` signal), so
        closing a task the engine drove never re-enters the reverse handler."""
        if not step or not step.task_id:
            return
        try:
            from tasks_management.models import Task
            task = Task.objects.filter(id=step.task_id).first()
            if task and task.status not in (Task.Status.COMPLETED, Task.Status.FAILED):
                task.status = Task.Status.FAILED if failed else Task.Status.COMPLETED
                task.save(username=self.user.username)
        except Exception as exc:
            logger.warning("approval: _close_task failed (%s)", exc)

    @register_service_signal('approval_service.finalized')
    def _finalize(self, approval_request, decision):
        """Terminal hook. Domain adapters bind to ``approval_service.finalized`` and act on the
        outcome (provision account, mark paylist approved, ...). The engine performs no domain
        logic itself."""
        return _ok(_("approval.finalized"),
                   {"id": str(approval_request.id), "decision": str(decision),
                    "domain": approval_request.flow.domain})

    # ---- helpers -----------------------------------------------------------
    def _get_request(self, request_id):
        return ApprovalRequest.objects.filter(id=request_id, is_deleted=False).first()

    def _user_obj(self):
        return self.user if isinstance(self.user, User) and getattr(self.user, 'id', None) else None
