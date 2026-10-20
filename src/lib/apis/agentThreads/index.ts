// [Gradient] A v2 agent thread's trace file, read from soev-api as the caller (routers/agent_threads.py).
import { WEBUI_API_BASE_URL } from '$lib/constants';

export const downloadAgentTrace = async (token: string, threadId: string): Promise<Blob> => {
	const res = await fetch(
		`${WEBUI_API_BASE_URL}/agent-threads/${encodeURIComponent(threadId)}/trace`,
		{ headers: { authorization: `Bearer ${token}` } }
	);
	if (!res.ok) throw new Error(`Trace download failed: HTTP ${res.status}`);
	return res.blob();
};
