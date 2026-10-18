// [Gradient] Previews of files whose original is still being prepared (a processing reference
// attachment) wait and retry with a bounded backoff instead of failing for good.

// Kept in sync with ORIGINAL_PROCESSING in backend/open_webui/soev/live_documents.py.
export const PREVIEW_PENDING_DETAIL = 'original_processing';
export const PREVIEW_RETRY_MAX_WAIT_MS = 180_000;

const PENDING_STATUSES = new Set(['pending', 'processing', 'uploading']);

/** Delay before retry `attempt` (0-based): 2s growing by 1.5x up to 10s. */
export function previewRetryDelay(attempt: number): number {
	return Math.min(10_000, Math.round(2_000 * 1.5 ** attempt));
}

/** True for the content route's "original still processing" answer (detail string or HTTP 409). */
export function isPreviewPending(error: unknown): boolean {
	if (error === PREVIEW_PENDING_DETAIL) return true;
	if (error && typeof error === 'object') {
		const { status, detail } = error as { status?: unknown; detail?: unknown };
		return status === 409 || detail === PREVIEW_PENDING_DETAIL;
	}
	return false;
}

/** True when a file's known status says its original may not exist yet. */
export function isPendingFileStatus(status: unknown): boolean {
	return typeof status === 'string' && PENDING_STATUSES.has(status);
}

export class PreviewPendingError extends Error {
	readonly status = 409;
	constructor() {
		super(PREVIEW_PENDING_DETAIL);
	}
}

export type PreviewRetryResult<T> =
	| { status: 'ok'; value: T }
	| { status: 'failed'; error: unknown }
	| { status: 'cancelled' };

export interface PreviewRetryOptions {
	/** False once the caller moved on (file switch, close, destroy); stops without reporting. */
	isCurrent: () => boolean;
	/** Called before each wait while the original is not available yet. */
	onPending?: () => void;
	isPending?: (error: unknown) => boolean;
	maxWaitMs?: number;
	delay?: (attempt: number) => number;
	sleep?: (ms: number) => Promise<void>;
	now?: () => number;
}

/** Runs `load` until it succeeds, fails with a non-pending error, the wait budget runs out, or the caller moves on. */
export async function loadWithPreviewRetry<T>(
	load: (attempt: number) => Promise<T>,
	{
		isCurrent,
		onPending,
		isPending = isPreviewPending,
		maxWaitMs = PREVIEW_RETRY_MAX_WAIT_MS,
		delay = previewRetryDelay,
		sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
		now = Date.now
	}: PreviewRetryOptions
): Promise<PreviewRetryResult<T>> {
	const started = now();
	for (let attempt = 0; ; attempt++) {
		try {
			const value = await load(attempt);
			return isCurrent() ? { status: 'ok', value } : { status: 'cancelled' };
		} catch (error) {
			if (!isCurrent()) return { status: 'cancelled' };
			const wait = delay(attempt);
			if (!isPending(error) || now() - started + wait > maxWaitMs) {
				return { status: 'failed', error };
			}
			onPending?.();
			await sleep(wait);
			if (!isCurrent()) return { status: 'cancelled' };
		}
	}
}
