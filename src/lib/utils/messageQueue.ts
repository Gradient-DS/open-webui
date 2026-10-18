import type { ChatAttachment } from '$lib/types/chatAttachment';

export type QueuedMessage = { id: string; prompt: string; files: ChatAttachment[] };
export type FileStatusEvent = {
	file_id: string;
	status: string;
	error?: string | null;
	collection_name?: string | null;
};

/** A file is not readable yet: uploading, or processing (an upload, or a OneDrive file attached by reference). */
export const isPending = (file: ChatAttachment): boolean =>
	file.status === 'uploading' || file.status === 'processing';

/** A message waits only while one of its files is still uploading or processing. */
export const isAwaitingUpload = (files: ChatAttachment[] | undefined): boolean =>
	(files ?? []).some(isPending);

/** Applies a `file:status` event to every queued message holding that file; returns `queues` itself when none does.
 * A failed file stays attached so the turn reports it as skipped. */
export const applyFileStatus = <Q extends Record<string, QueuedMessage[]>>(
	queues: Q,
	event: FileStatusEvent
): Q => {
	if (event.status !== 'completed' && event.status !== 'failed') return queues;
	const holds = (message: QueuedMessage) =>
		(message.files ?? []).some((file) => file.id === event.file_id);
	if (!Object.values(queues).some((queue) => (queue ?? []).some(holds))) return queues;

	const settle = (file: ChatAttachment): ChatAttachment =>
		file.id !== event.file_id
			? file
			: event.status === 'completed'
				? {
						...file,
						status: 'uploaded',
						...(event.collection_name ? { collection_name: event.collection_name } : {})
					}
				: { ...file, status: 'failed', error: event.error ?? '' };

	return Object.fromEntries(
		Object.entries(queues).map(([chatId, queue]) => [
			chatId,
			(queue ?? []).map((message) =>
				holds(message) ? { ...message, files: message.files.map(settle) } : message
			)
		])
	) as Q;
};

/** Puts an upload's result into the queued message holding its item. An upload that failed outright has no
 * server record and is dropped, as the composer drops it. */
export const applyUploadResult = (queue: QueuedMessage[], file: ChatAttachment): QueuedMessage[] =>
	queue.map((message) =>
		(message.files ?? []).some((item) => item.itemId === file.itemId)
			? {
					...message,
					files:
						file.status === 'error'
							? message.files.filter((item) => item.itemId !== file.itemId)
							: message.files.map((item) => (item.itemId === file.itemId ? file : item))
				}
			: message
	);
