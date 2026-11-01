import type { ProjectionPage } from '$lib/apis/threads';

export type ThreadRegeneration = {
	content: string;
	files?: unknown;
	model: string;
	features: Record<string, unknown>;
	regenerate_from: { thread_id: string; position: number };
};

// Navigation can remount Chat; consume the replay only after its fork has loaded.
const pending = new Map<string, ThreadRegeneration>();
export const queueThreadRegeneration = (id: string, input: ThreadRegeneration) =>
	pending.set(id, input);
export const takeThreadRegeneration = (id: string) => {
	const input = pending.get(id);
	pending.delete(id);
	return input;
};

export const threadRegenerationInput = (
	page: ProjectionPage,
	answerModel: string,
	features: Record<string, unknown>
): { at: number; input: ThreadRegeneration } => {
	const views = [...page.messages].sort((a, b) => a.position - b.position);
	const user = [...views]
		.reverse()
		.find((view) => (view.content as { role?: string })?.role === 'user');
	const fields = user?.content as {
		text?: string;
		content?: string;
		files?: unknown;
	};
	if (!user || user.position <= 1 || !views.at(-1)?.finished)
		throw new Error('Reload this chat to continue');
	return {
		at: user.position - 1,
		input: {
			content: fields.content ?? fields.text ?? '',
			files: fields.files,
			model: answerModel,
			features: structuredClone(features),
			regenerate_from: { thread_id: page.thread_id, position: user.position }
		}
	};
};
