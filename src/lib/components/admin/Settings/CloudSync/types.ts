// Shared types for the descriptor-driven Cloud Sync admin panel.
//
// The building blocks (ProviderCard, SyncSettingsSection, SharedKbSection),
// the per-provider section components (Confluence/GoogleDrive/OneDrive) and the
// CloudSync.svelte orchestrator all consume these. These are the canonical
// config-response shapes — the former monolith's inline copies were removed in
// the Phase 1.3 rewrite.

import type { ComponentType, SvelteComponent } from 'svelte';

// ─────────────────────────────────────────────────────────────────────
// Provider descriptor — one entry per cloud-sync provider. The accordion
// orchestrator (Phase 1.3) iterates an array of these to render a
// ProviderCard + the provider's section component for each.
// ─────────────────────────────────────────────────────────────────────

export type ItemNoun = 'pages' | 'files' | 'items';

export type AuthMode = 'oauth' | 'basic' | 'scoped';

// Sync run status. 'idle' | 'syncing' are the known values; `(string & {})`
// keeps the type open to server-added values while preserving literal
// autocomplete.
export type SyncRunStatus = 'idle' | 'syncing' | (string & {});

export interface ProviderDescriptor {
	// Stable provider key — matches the cloud-sync status endpoint slug
	// (confluence, google_drive, onedrive, ...).
	slug: string;
	// Human-readable name (already translated or an i18n key the caller
	// resolves).
	name: string;
	// Provider icon component (rendered `size-5` in the card header).
	icon: ComponentType<SvelteComponent<{ className?: string }>>;
	// What a single synced unit is called — drives sync-settings labels.
	itemNoun: ItemNoun;
	// ── Informational capability matrix ─────────────────────────────────
	// The fields below document each provider's capabilities; they are NOT
	// behavioral switches. The section components own the actual behavior
	// (test-connection affordance, shared-KB flow, auth-mode UI) — setting a
	// flag here does not toggle any feature. Kept as the planned capability
	// model for future providers.
	//
	// Whether the provider exposes a "Test connection" affordance.
	hasTestConnection: boolean;
	// Whether the provider can serve one pre-synced shared knowledge base.
	supportsSharedKb: boolean;
	// Supported authentication modes, when the provider offers a choice.
	authModes?: AuthMode[];
}

// ─────────────────────────────────────────────────────────────────────
// Cross-provider status — one entry per provider slug, returned by
// `GET /api/v1/configs/cloud-sync/status` (`getCloudSyncStatus`). Shape
// matches `_aggregate_provider_status` in backend/routers/configs.py.
// ─────────────────────────────────────────────────────────────────────

export interface CloudSyncProviderStatus {
	kb_count: number;
	file_count: number;
	last_sync_at: number | null;
	// Aggregate status — 'idle' | 'syncing' (more may be added server-side).
	status: SyncRunStatus;
	syncing: boolean;
	suspended_count: number;
	// True when any KB of this provider is a shared (org-wide) KB.
	shared: boolean;
}

// Whole-endpoint payload: status keyed by provider slug.
export type CloudSyncStatusResponse = Record<string, CloudSyncProviderStatus>;

// ─────────────────────────────────────────────────────────────────────
// Section instance contract — the orchestrator drives every provider
// section through these two methods (via `bind:this`), regardless of
// provider. Each section component exports `load()`/`persist()`; this is
// the structural shape the slug-keyed `sectionRefs` map holds.
// ─────────────────────────────────────────────────────────────────────

export interface CloudSyncSection {
	load: () => Promise<void>;
	persist: () => Promise<void>;
}

// ─────────────────────────────────────────────────────────────────────
// Provider config response shapes — mirror the `/configs/{provider}` GET
// payloads. Consumed by the per-provider section components.
// ─────────────────────────────────────────────────────────────────────

export interface ConfluenceConfigResponse {
	ENABLE_CONFLUENCE_INTEGRATION?: boolean;
	ENABLE_CONFLUENCE_SYNC?: boolean;
	CONFLUENCE_OAUTH_CLIENT_ID?: string;
	CONFLUENCE_OAUTH_CLIENT_SECRET?: string;
	CONFLUENCE_SYNC_INTERVAL_MINUTES?: number;
	CONFLUENCE_MAX_PAGES_PER_SYNC?: number;
	CONFLUENCE_AUTH_MODE?: string;
	CONFLUENCE_SITE_URL?: string;
	CONFLUENCE_BASIC_AUTH_USERNAME?: string;
	CONFLUENCE_BASIC_AUTH_API_TOKEN?: string;
	CONFLUENCE_SCOPED_API_TOKEN?: string;
	CONFLUENCE_CLOUD_ID?: string;
	CONFLUENCE_KB_MODE?: string;
}

export interface GoogleDriveConfigResponse {
	ENABLE_GOOGLE_DRIVE_INTEGRATION?: boolean;
	ENABLE_GOOGLE_DRIVE_SYNC?: boolean;
	GOOGLE_DRIVE_CLIENT_ID?: string;
	GOOGLE_DRIVE_API_KEY?: string;
	GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES?: number;
	GOOGLE_DRIVE_MAX_FILES_PER_SYNC?: number;
}

export interface OneDriveConfigResponse {
	ENABLE_ONEDRIVE_INTEGRATION?: boolean;
	ENABLE_ONEDRIVE_SYNC?: boolean;
	ENABLE_ONEDRIVE_PERSONAL?: boolean;
	ENABLE_ONEDRIVE_BUSINESS?: boolean;
	ONEDRIVE_CLIENT_ID_PERSONAL?: string;
	ONEDRIVE_CLIENT_ID_BUSINESS?: string;
	ONEDRIVE_SHAREPOINT_URL?: string;
	ONEDRIVE_SHAREPOINT_TENANT_ID?: string;
	ONEDRIVE_SYNC_INTERVAL_MINUTES?: number;
	ONEDRIVE_MAX_FILES_PER_SYNC?: number;
}

// ─────────────────────────────────────────────────────────────────────
// SharedKbSection injected API — the provider-specific async functions
// the section drives. The orchestrator (Phase 1.3) passes the provider's
// client functions (e.g. Confluence's). `Status` is the provider's
// shared-KB status shape (e.g. `ConfluenceSharedKbStatus`); kept generic
// so the section does not hardcode any provider.
// ─────────────────────────────────────────────────────────────────────

export interface SharedKbApi<Status, ProvisionPayload> {
	getStatus: () => Promise<Status>;
	provision: (payload: ProvisionPayload) => Promise<Status>;
	sync: () => Promise<unknown>;
	remove: () => Promise<unknown>;
}

// Minimal shape SharedKbSection reads off the provider status to drive its
// UI. Providers' richer status types (e.g. `ConfluenceSharedKbStatus`)
// structurally satisfy this.
export interface SharedKbStatusLike {
	provisioned: boolean;
	// 'idle' | 'syncing' (more may be added server-side).
	status?: SyncRunStatus;
	last_sync_at?: number | null;
	suspended_at?: number | null;
	// Why the KB was suspended (e.g. 'service_credential_invalid',
	// 'owner_access_lost') and how many days until the managed-shared-KB
	// auto-delete window — both surfaced in the suspended-state explanation.
	suspended_reason?: string | null;
	days_remaining?: number | null;
	file_count?: number;
	progress_current?: number;
	progress_total?: number;
}
