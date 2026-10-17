// [Gradient] A stopped turn is settled once the backend sends `chat:tasks:cancel` for its message: on the agent
// route that event follows the cancel at soev-api and the stored thread bookmark, which the next turn forks from.
const waiters = new Map<string, () => void>();

/** Resolve once `markSettled(messageId)` runs, or with false after `timeoutMs`. Call before stopping the turn. */
export const expectSettled = (messageId: string, timeoutMs: number): Promise<boolean> =>
	new Promise((resolve) => {
		const timer = setTimeout(() => {
			waiters.delete(messageId);
			resolve(false);
		}, timeoutMs);
		waiters.set(messageId, () => {
			clearTimeout(timer);
			waiters.delete(messageId);
			resolve(true);
		});
	});

export const markSettled = (messageId: string | null | undefined) => {
	if (messageId) waiters.get(messageId)?.();
};
