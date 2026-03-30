### Current State

<!-- Mutable — update frequently, remove items when resolved. See methodology.md Section 6. -->

#### Current Work

- External agents package — active development and integration
- New cloud integrations (Google Drive recently completed, exploring more)
- Regular upstream merges to stay current (latest: v0.6.43 → v0.8.9, branch `merge/upstream-260329`)

#### Pending Fixes

- **Cloud KB permission leak**: `_sync_permissions()` in both sync workers needs rewrite — should only verify owner access, not mirror cloud sharing into access grants. After code fix, run cleanup on gradient.soev.ai (see `fix_kb_gradient_soev.md`). Migration hardening (NULL knowledge → private in `f1e2d3c4b5a6`) already applied on `feat/logos` branch.

#### Open Questions

#### Active Resources

- Sync abstraction cookbook: `collab/docs/external-integration-cookbook.md`
- Upstream merge research: `thoughts/shared/research/2026-03-20-upstream-merge-strategy.md`
