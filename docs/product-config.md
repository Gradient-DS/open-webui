# Product configuration

With `AGENT_API_ENABLED` and `SOEV_API_URL` configured, OWUI reads
`GET /v1/products/owui/config` with its service credential. A successful snapshot
is cached for 60 seconds. A failed refresh keeps the last successful snapshot
and retries after 10 seconds. Without a successful snapshot, product reads
raise `service_unavailable`; environment variables and stored rows do not supply
product values. Changing the backend URL or credential discards the snapshot.

`soev/product_config_schema.json` is the exact key/type projection of
`soev_platform.contract.owui.settings`. Registry mismatches fail explicitly.
Retired feature and permission entries resolve disabled on the platform route.
An unentitled feature resolves disabled even when deployment or stored values
would enable it. Permission resolution uses the individual `permissions.*`
leaves, including for the aggregate `user.permissions` read. Local group and
default permission rows do not contribute; resource grants and memberships do.

Every other persistent key reads the environment-derived `Config.DEFAULTS`.
`Config.upsert` (this fork has no `Config.set`) refuses platform writes atomically,
including writes to deployment-owned keys. Legacy config-row repairs and default
seeding do not run on the platform route. `/api/config` retains its existing shape.

`models.overrides` maps catalog IDs to optional `name`, `base_model_id`, `params`,
`meta` and `is_active` fields. The prompt is `params.system`; null or omission
uses the global product prompt. Catalog rows in the local database supply none
of these values. Without an override, the catalog ID is the name, parameters and
metadata are empty, and the model is active. Catalog capabilities still constrain
metadata. Assistant rows keep their own content and grants. Unknown catalog IDs
in the map remain inert until the catalog offers them.

The console's `ui.default_models` supplies `/api/config.default_models`, including
an explicit empty selection; neither the local value nor the catalog's default
replaces it.

## Internal writers

The following non-admin calls to `Config.upsert` were inspected:

| Caller | Platform behavior |
| --- | --- |
| `config.import_legacy_config_json` | Refuses the legacy import; preserves the source file. |
| `main.lifespan` and `routers/auths.py` first-user signup disabling | Refuses the signup write if this legacy provisioning path is invoked. |
| `main.initialize_runtime_config`, tool-server grant migration | Refuses if a deployment connection still contains legacy access control. |
| `main.initialize_runtime_config`, arena grant migration | Legacy migration does not run; product content must already use access grants. |
| `main` OAuth client re-registration | Refuses persistence and reports re-registration failure. Deployment connections cannot acquire runtime config writes. |
| `events.migrate_legacy_webhook_config` | Reads product webhooks without importing the legacy URL. |
| `events.upsert_event_webhook` / `delete_event_webhook` | Admin HTTP callers receive the explicit read-only refusal. |

Other `Config.upsert` callers are administrator configuration endpoints; their
writes are refused. No accepted write is silently discarded. Personal settings
use separate user storage and continue to work. The legacy route retains its
existing configuration, feature and permission behavior.

The security inventory is `security/product-config-coverage.toml`. The product
configuration sweep seed in soev-solutions writes and restores model content
through console authority. Skill-file route seeds require skills and skill files
to be entitled and enabled in product configuration. No sweep turns those gates
on by writing OWUI settings.

Skill-file routes have stable OpenAPI declarations. Outside the platform route,
their matcher preserves the original 404 behavior when `FEATURE_SKILL_FILES` is
off; on the platform route the live product gates control access. Scoped content
tokens retain their existing authentication path.
