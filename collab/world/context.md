### Context

<!-- Tier 1 — ~5,000 char cap. See methodology.md Section 4. -->

#### Personal

Lex Lubbers (@lexlubbers) — developer on soev.ai, working across the full stack (SvelteKit frontend, FastAPI backend, Helm deployments). Hands-on with all custom features and upstream merges.

#### Organization

Soev.ai — SaaS platform focused on the Dutch public sector and data sovereignty. Provides AI chat capabilities with enterprise-grade data management, compliance features (GDPR archival), and integration with organizational tools (OneDrive, Google Drive).

#### Project(s)

This repo is a fork of Open WebUI, customized for soev.ai. Key additions beyond upstream:
- Cloud sync integrations (OneDrive, Google Drive) via a shared sync abstraction layer
- Feature flags for granular admin control over UI features
- GDPR-compliant user archival
- Email invites via Microsoft Graph
- External agent API (proxy + internal routing)
- External pipeline/integration providers for RAG processing
- Typed knowledge bases (local, onedrive, google_drive, custom)
- Acceptance modal and feedback configuration

The goal is to maintain a clean fork that can regularly merge upstream Open WebUI changes with minimal conflicts.

#### Constraints

- **Upstream compatibility**: All custom features must be additive — avoid modifying upstream code paths where possible. Use feature flags, conditional mounts, and separate files/routers to minimize merge conflicts.
- **Data sovereignty**: Core value proposition — security and data handling decisions carry extra weight.
- **Dutch public sector**: Compliance requirements (GDPR), organizational tooling (Microsoft ecosystem, Google Workspace).

#### Technology Stack

- **Frontend**: SvelteKit 5 (runes), Vite, Tailwind CSS 4, bits-ui
- **Backend**: FastAPI, SQLAlchemy (async), Socket.IO, aiohttp
- **Database**: SQLite/PostgreSQL with Alembic migrations
- **Deployment**: Helm charts, Docker
- **Integrations**: Microsoft Graph API (OneDrive, email), Google Drive API v3, external agent services
