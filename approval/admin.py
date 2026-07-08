from django.contrib import admin

from approval.models import ApprovalFlow, ApprovalRequest, ApprovalStep, ApprovalDecision

admin.site.register(ApprovalFlow)
admin.site.register(ApprovalRequest)
admin.site.register(ApprovalStep)
admin.site.register(ApprovalDecision)
