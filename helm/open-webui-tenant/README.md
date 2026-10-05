# Open WebUI tenant chart

Existing dedicated tenant namespaces keep `networkPolicy.scope: namespace`.
For v2 in a shared `soev-<client>` namespace, set `networkPolicy.scope: release`
and `secretStoreRef` to the platform's existing SecretStore or ClusterSecretStore.
See [ci/v2-values.yaml](ci/v2-values.yaml) for a complete rendering example.

## Platform settings

These values are at the chart root. Unset values emit no environment variable;
the application supplies its defaults. Explicit `false` for either live feature
is rendered as `"false"`.

| Value | Environment variable | Default when unset |
| --- | --- | --- |
| `soevApi.url` | `SOEV_API_URL` | Empty |
| `soevApi.audience` | `SOEV_API_AUDIENCE` | Empty |
| `soevApi.servicePrincipal` | `SOEV_API_SERVICE_PRINCIPAL` | Empty |
| `agentApi.runtime` | `AGENT_API_RUNTIME` | `v1`; accepts `v1` or `v2` |
| `liveDocuments.enabled` | `ENABLE_LIVE_DOCUMENTS` | `false` |
| `liveMail.enabled` | `ENABLE_LIVE_MAIL` | `false` |
| `webuiUrl` | `WEBUI_URL` | Empty |

With `externalSecrets.enabled: true` and a nonempty `soevApi.url`, the
ExternalSecret fetches `soevApiKey` and `soevApiSigningKey` from the configured
1Password item. The application receives them as `SOEV_API_KEY` and
`SOEV_API_SIGNING_KEY` through `secretKeyRef`. Store the signing key as the
literal multiline Ed25519 PEM; no template or newline substitution is applied.
When External Secrets is disabled, use `secrets.soevApiKey` and
`secrets.soevApiSigningKey` (a YAML `|` block preserves PEM newlines).

`extraEnv` is appended to the main Open WebUI container's environment. Each
entry supports either `{name, value}` or `{name, valueFrom}`; quote scalar
values as strings. It does not modify the migration init container.

## PostgreSQL vectors

Set `openWebui.config.vectorDb: pgvector` and configure `postgres.host`,
`postgres.port`, `postgres.user`, and `postgres.database` for CNPG, with
`postgres.enabled: false`. `PGVECTOR_DB_URL` inherits the application's
`DATABASE_URL`. `DATABASE_USER` and `DATABASE_NAME` always come from
`openWebui.config.databaseUser` and `openWebui.config.databaseName`. The app can
reconstruct its URL from these variables, so for pgvector set them to match
`postgres.user` and `postgres.database`, respectively. Rendering fails on a
mismatch; the chart does not override either value.

`pgvector.maxVectorLength` defaults to `1024` (bge-m3 dimensions) and sets
`PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH`; choose a matching embedding model.
`pgvector.createExtension` defaults to `false` and sets
`PGVECTOR_CREATE_EXTENSION`. CNPG must declare the `vector` extension before
Open WebUI starts; the owner role cannot create extensions. Both settings are
emitted only in pgvector mode. Set `weaviate.enabled: false` for pgvector;
rendering fails if it is true. The bundled Weaviate StatefulSet, Service, and
wait container follow `weaviate.enabled`.

## Secret stores and policy scope

`secretStoreRef: {kind: ClusterSecretStore, name: soev-staging}` makes every
ExternalSecret use that store and suppresses the chart's own SecretStore.
`kind: SecretStore` also works for an existing namespaced store. An empty
reference preserves the chart-managed 1Password SecretStore.

`networkPolicy.scope` accepts `namespace` (default) or `release`. Release scope
sets both the Kubernetes `podSelector` and Cilium `endpointSelector` to
`app.kubernetes.io/instance: <release>`. All chart pod templates carry that
label, including Redis, PostgreSQL, Weaviate, loader-worker, its bootstrap Job,
and sync-daemon. Ingress and egress rules are identical in both modes.

Keep namespace scope for existing dedicated tenant namespaces: the sibling
`agent-stack` chart has its own NetworkPolicy, but `tenant-backup` and
`tenant-deactivation` do not and can rely on this chart's namespace policy.

## Database migrations

Every Open WebUI pod runs `python -c 'import open_webui.config'` in a `migrate`
init container on both first install and upgrades. It uses the application
image, resources, security context, ConfigMap, database secret references, and
data volume. The init container sets `ENABLE_DB_MIGRATIONS=true`; the main
container inherits `false` from the ConfigMap. Kubernetes waits for the
ExternalSecret's generated Secret before starting the init container.

Use an application image containing the PostgreSQL migration advisory lock
when running multiple replicas. The lock covers the entire online Alembic run
and survives migration commits and rollbacks; SQLite is unchanged. There is
no remaining Peewee migration runner in this backend.

The former migration hook and its values have been removed. Init containers
retry under the pod's restart policy and use `openWebui.resources`; there is no
separate hook deadline or resource configuration. For multiple replicas, also
enable Redis and disable the default RWO data PVC, as in the v2 example.

## v1 to v2 migration Job

`soevApi.migrate.enabled` renders a plain `batch/v1` Job (not a Helm hook)
named `<release>-v2-migrate-<hash>`. The hash covers the image tag and all
`soevApi.migrate` values, so a new image or new values start a new run; each
step is idempotent, and the once-only steps (config switch, model ids,
sign-out) are skipped on later runs with the same `soevApi.migrate.id`.

The Job runs `python -m open_webui.soev.migrate --<mode>` with the app's
ConfigMap, secrets, `extraEnv` and data volume, plus `ENABLE_DB_MIGRATIONS=true`
so the schema is current. `soevApi.migrate.config` becomes `SOEV_V2_CONFIG` and
`modelMap` becomes `SOEV_V2_MODEL_MAP`. Exit 1 (soev-api unreachable, collection
mismatch) and 75 (ingest still running) are retried with Kubernetes' capped
exponential backoff up to `backoffLimit`; exit 2 (invalid input) fails the Job
at once. Redis must be enabled for the final sign-out. With an RWO data PVC the
Job pod must land on the app's node; v2 tenants run without one.

Set `mode: restore` to put back the config snapshot and model ids before
reverting a cutover. The steps are in `docs/agent-api-deployment.md`.

## Render checks

```sh
helm lint helm/open-webui-tenant
for values in helm/open-webui-tenant/ci/*.yaml; do
  helm template o1-chart helm/open-webui-tenant -n soev-v2 -f "$values"
done
```

The focused backend test is
`backend/open_webui/test/migrations/test_migration_lock.py`. Set
`MIGRATION_LOCK_TEST_DATABASE_URL` to an isolated PostgreSQL database to also
exercise real concurrent sessions, commit boundaries, and failure cleanup.
