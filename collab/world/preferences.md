### User Preferences

<!-- Tier 1 — ~5,000 char cap. See methodology.md Section 4. -->

#### Communication

- Wants to understand the "why" behind changes — explain how decisions affect security, performance, and abstraction levels
- Discuss extendability implications: will this pattern scale to future integrations? Will it cause merge pain?
- Concise but substantive — no fluff, but do surface trade-offs and architecture considerations

#### Code Style

- Follow existing codebase patterns (see CLAUDE.md for conventions)
- Prefer additive changes over modifying upstream code — separate files, conditional mounts, feature flags
- Use the sync abstraction layer pattern for new integrations (see `collab/docs/external-integration-cookbook.md`)
- **i18n: all new user-facing text must include Dutch (nl-NL) translations** — add entries to both `en-US/translation.json` and `nl-NL/translation.json`. Keys are alphabetically sorted; empty string in en-US means "use the key itself".

#### Working Approach

- Build for upstream merge compatibility — minimize touch points with upstream files
- Feature flags for anything an admin might want to disable
- When building integrations, follow the established Template Method + Factory pattern in the sync abstraction layer
- **Always preserve custom changes** — when upstream merge conflicts arise, keep our customizations while staying as close as possible to upstream. Never silently drop custom code in favor of upstream.
