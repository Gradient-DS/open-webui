import { WEBUI_API_BASE_URL } from '$lib/constants';

export type CloudProvider = 'onedrive' | 'google_drive';
export type ScheduleKind = 'content' | 'acl_refresh';
export type ScheduleAction = 'run' | 'cancel' | 'suspend' | 'resume';

export interface Connection {
	id: string;
	source_kind: string;
	lifecycle: string;
	last_error?: string | null;
}

export interface Authorization {
	authorize_url: string;
	expires_at?: string;
}

export interface ScheduleForm {
	connection_id: string;
	kind: ScheduleKind;
	scope: Record<string, string | boolean | null>;
	cadence_minutes?: number;
	label?: string | null;
	path?: string | null;
}

export type RunOutcome = 'succeeded' | 'partial' | 'failed' | 'cancelled';

// soev-api serialises StoredRun verbatim: a live run has `outcome: null` and no
// `finished_at`. There is no `status` field — derive one with `runStatus()`.
export interface SyncRun {
	id: string;
	started_at: string;
	finished_at?: string | null;
	outcome?: RunOutcome | null;
	error_code?: string | null;
	cancel_requested_at?: string | null;
	counts?: Record<string, number>;
	[key: string]: unknown;
}

export interface Schedule extends ScheduleForm {
	subscribers: string[];
	subscriber_count: number;
	last_error?: string | null;
	id: string;
	source_kind: string;
	lifecycle: string;
	last_run?: SyncRun | null;
	next_due_at?: string | null;
	provider_secret_days_to_expiry?: number | null;
	connection: Connection;
}

export class CloudSyncError extends Error {
	constructor(
		public status: number,
		public code: string,
		message: string,
		public constraint?: string
	) {
		super(message);
		this.name = 'CloudSyncError';
	}
}

async function request<T>(token: string, path: string, method = 'GET', body?: unknown): Promise<T> {
	const response = await fetch(`${WEBUI_API_BASE_URL}/cloud-sync${path}`, {
		method,
		headers: {
			Authorization: `Bearer ${token}`,
			...(body === undefined ? {} : { 'Content-Type': 'application/json' })
		},
		...(body === undefined ? {} : { body: JSON.stringify(body) })
	});
	if (!response.ok) {
		const problem = await response.json().catch(() => null);
		const detail = typeof problem?.detail === 'object' ? problem.detail : problem;
		throw new CloudSyncError(
			response.status,
			typeof detail?.code === 'string' ? detail.code : 'request_failed',
			typeof detail?.detail === 'string' ? detail.detail : `HTTP ${response.status}`,
			typeof detail?.constraint === 'string' ? detail.constraint : undefined
		);
	}
	return response.status === 204 ? (undefined as T) : response.json();
}

const connectionPath = (id: string) => `/connections/${encodeURIComponent(id)}`;
const knowledgePath = (id: string) => `/knowledge/${encodeURIComponent(id)}`;
const schedulePath = (knowledgeId: string, id: string) =>
	`${knowledgePath(knowledgeId)}/schedules/${encodeURIComponent(id)}`;

export const createConnection = (token: string, provider: CloudProvider) =>
	request<Authorization & { connection_id: string }>(token, '/connections', 'POST', { provider });
export const listConnections = (token: string) => request<Connection[]>(token, '/connections');
export const getConnection = (token: string, id: string) =>
	request<Connection>(token, connectionPath(id));
export const authorizeConnection = (token: string, id: string) =>
	request<Authorization>(token, `${connectionPath(id)}/authorize`, 'POST');
export const revokeConnection = (token: string, id: string) =>
	request<void>(token, connectionPath(id), 'DELETE');
export const createSchedule = (token: string, knowledgeId: string, form: ScheduleForm) =>
	request<Schedule>(token, `${knowledgePath(knowledgeId)}/schedules`, 'POST', form);
export const deleteSchedule = (token: string, knowledgeId: string, id: string) =>
	request<void>(token, schedulePath(knowledgeId, id), 'DELETE');
export const getSyncStatus = (token: string, knowledgeId: string) =>
	request<{ schedules: Schedule[] }>(token, `${knowledgePath(knowledgeId)}/sync`);
export const runSchedule = (token: string, knowledgeId: string, id: string) =>
	request<{ job_id: string }>(token, `${schedulePath(knowledgeId, id)}/run`, 'POST');
export const cancelSchedule = (token: string, knowledgeId: string, id: string) =>
	request<void>(token, `${schedulePath(knowledgeId, id)}/cancel`, 'POST');
export const suspendSchedule = (token: string, knowledgeId: string, id: string) =>
	request<void>(token, `${schedulePath(knowledgeId, id)}/suspend`, 'POST');
export const resumeSchedule = (token: string, knowledgeId: string, id: string) =>
	request<void>(token, `${schedulePath(knowledgeId, id)}/resume`, 'POST');
