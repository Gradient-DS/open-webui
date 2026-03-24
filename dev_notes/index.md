# Open WebUI (Gradient-DS Fork) — Dev Notes Index

| Date | Title | Dev | Summary | Keywords |
|------|-------|-----|---------|----------|
| 20-03-2026 | Gradient-DS Custom Features Overview | @lexlubbers | Documented all 9 custom features built on top of Open WebUI: Typed KBs, OneDrive, Email Invites, GDPR Archival, Acceptance Modal, Feature Flags, Feedback Config, External Pipeline/Integration Providers, and Agent API. All survived the v0.6.43 → v0.8.9 upstream merge. | custom-features, upstream-merge, onedrive, feature-flags, agent-api, knowledge-types, gdpr, invites |
| 24-03-2026 | External Integration Cookbook | @lexlubbers | Step-by-step recipe (12 steps) for adding new cloud sync providers to the sync abstraction layer. Covers backend (config, API client, auth, token refresh, sync worker, provider, factory, router, main.py) and frontend (API client, picker, KnowledgeBase.svelte). Includes architecture diagram, abstract method reference, provider comparison table. | sync-abstraction, integration-cookbook, BaseSyncWorker, SyncProvider, cloud-sync, google-drive, onedrive |
