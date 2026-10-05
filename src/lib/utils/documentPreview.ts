export const normalizeDocumentTargetPage = (page: unknown): number | null => {
	if (page === undefined || page === null || page === '') {
		return null;
	}

	const value = Number(page);
	if (!Number.isFinite(value)) {
		return null;
	}

	const targetPage = Math.trunc(value);
	return targetPage > 0 ? targetPage : null;
};

export const clampDocumentTargetPage = (page: number | null | undefined, pageCount: number) => {
	if (!page || pageCount < 1) {
		return null;
	}

	return Math.min(Math.max(1, page), pageCount);
};

// Coalesce streaming snapshots without postponing updates while deltas keep arriving.
export function documentPreviewThrottle<T>(render: (value: T) => void, interval = 500) {
	let lastRender = -Infinity;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let latest: T;
	const flush = () => {
		clearTimeout(timer);
		timer = undefined;
		lastRender = Date.now();
		render(latest);
	};
	return {
		update(value: T, done: boolean) {
			latest = value;
			const wait = interval - (Date.now() - lastRender);
			if (done || wait <= 0) flush();
			else if (timer === undefined) timer = setTimeout(flush, wait);
		},
		destroy() {
			clearTimeout(timer);
		}
	};
}
