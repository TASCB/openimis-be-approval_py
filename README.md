# openimis-be-approval_py

Generic **Approval Engine** module for openIMIS (TASAF / CoreMIS). Owns approval
state, steps, decisions and audit history for domain entities referenced by
`ContentType + object_id`.

Domain modules request approval and bind an adapter to the
`approval_service.finalized` service signal. They should not create their own
approval tables for the same workflow.

Module number **24** (rights `24xxxx`, engine operations only; step authorization uses the target
domain's own rights). See `docs/APPROVAL_ENGINE_DESIGN.md` in the dist repo for the full design.

Implemented surface: models, migrations, service methods
(`request/approve/reject/cancel/return`, `get_pending_for_user`), GraphQL
queries/mutations, rights seeding, code-seeded flows, and admin flow editing.

## Developer Guide

Use `approval/services.py` for workflow behavior. `ApprovalFlow.config.steps`
defines ordered steps; each step's `required_right` is a domain right such as
`230201`, not an approval-module right. The `24xxxx` rights only control engine
administration and dashboard access.

Default flows live in `approval/apps.py::DEFAULT_FLOWS` and are seeded on
`post_migrate`. Once a flow is edited in the UI, `is_user_managed=True` prevents
future migrations from overwriting it.

Domain integration pattern:

1. Call the service to request approval for a domain object.
2. Listen to `approval_service.finalized`.
3. Update the domain object in the module adapter.

Keep frontend constants, backend rights, and seeded flow `required_right` values
in sync when adding or changing a flow.
