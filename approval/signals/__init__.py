"""Cross-module service-signal bindings for the Approval Engine.

Two directions:

1. **Outbound (domain hook):** the engine FIRES ``approval_service.finalized`` on final
   approval/rejection/cancellation. Domain modules (access_request, tasaf_payment, training, ...)
   bind their OWN adapter to it in their OWN ``signals/__init__.py`` — the engine binds nothing
   outbound and never imports domain code. Example:

       bind_service_signal('approval_service.finalized',
                           AccessRequestApprovalAdapter.on_finalized,
                           bind_type=ServiceSignalBindType.AFTER)

2. **Inbound (inbox → decision):** when an approver completes their ``tasks_management`` inbox task
   (Approve = COMPLETED, Decline = FAILED), translate it into an ``ApprovalService`` decision. This
   is what lets approvers act from the generic Tasks inbox as well as the (future) approval UI.
"""
import logging

logger = logging.getLogger(__name__)


def on_approval_task_complete(**kwargs):
    """Bound to ``task_service.complete_task``. Turns completion of an ``approval.*`` task into an
    approve/reject decision. Guarded by the current-step check inside ``ApprovalService`` so it is
    idempotent — if the engine already recorded the decision (mutation-driven), this no-ops."""
    try:
        from tasks_management.models import Task
        from core.models import User
        from approval.services import ApprovalService

        result = kwargs.get('result', {})
        if not result or not result.get('success'):
            return
        task = result['data']['task']
        business_event = task.get('business_event') or ''
        if not business_event.startswith('approval.'):
            return  # not an approval task — leave it for other handlers

        data = task.get('data') or {}
        request_id = data.get('approval_request_id')
        step_id = data.get('approval_step_id')
        if not request_id or not step_id:
            logger.warning("approval: task %s missing approval ids", task.get('id'))
            return

        user = User.objects.get(id=result['data']['user']['id'])
        service = ApprovalService(user)
        failed = task.get('status') == Task.Status.FAILED
        if failed:
            service.reject(request_id, step_id, comment='Declined from Tasks inbox')
        else:
            service.approve(request_id, step_id, comment='Approved from Tasks inbox')
    except Exception as exc:
        logger.error("approval: on_approval_task_complete failed", exc_info=exc)
        return [str(exc)]


def bind_service_signals():
    from core.service_signals import ServiceSignalBindType
    from core.signals import bind_service_signal
    bind_service_signal(
        'task_service.complete_task',
        on_approval_task_complete,
        bind_type=ServiceSignalBindType.AFTER,
    )
