export type TurnErrorDetails = {
	code?: string;
	status?: number;
	upstream_status?: number;
	trace_id?: string;
};

export const turnErrorDetails = (value: unknown): TurnErrorDetails => {
	if (typeof value !== 'object' || value === null) return {};
	const error = value as Record<string, unknown>;
	const details: TurnErrorDetails = {};
	if (typeof error.code === 'string' && /^[a-z][a-z0-9_]{0,127}$/.test(error.code)) {
		details.code = error.code;
	}
	for (const key of ['status', 'upstream_status'] as const) {
		const status = error[key];
		if (typeof status === 'number' && Number.isInteger(status) && status >= 100 && status <= 599) {
			details[key] = status;
		}
	}
	if (typeof error.trace_id === 'string' && /^[0-9a-f]{32}$/.test(error.trace_id)) {
		details.trace_id = error.trace_id;
	}
	return details;
};
