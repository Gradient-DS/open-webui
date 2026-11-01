import type { ProjectionPage } from '$lib/apis/threads';

export type ThreadRegeneration = {
	content: string;
	files?: unknown;
	model: string;
	features: Record<string, unknown>;
	regenerate_from?: { thread_id: string; position: number };
	edit_from?: { thread_id: string; position: number };
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
		features?: Record<string, unknown>;
	};
	if (!user || user.position <= 1 || !views.at(-1)?.finished)
		throw new Error('Reload this chat to continue');
	return {
		at: user.position - 1,
		input: {
			content: fields.content ?? fields.text ?? '',
			files: fields.files,
			model: answerModel,
			features: structuredClone(fields.features ?? features),
			regenerate_from: { thread_id: page.thread_id, position: user.position }
		}
	};
};

export const threadEditInput = (
	page: ProjectionPage,
	position: number,
	content: string,
	model: string,
	features: Record<string, unknown>
): { at: number; input: ThreadRegeneration } => {
	const user = page.messages.find((view) => view.position === position);
	const fields = user?.content as {
		role?: string;
		files?: unknown;
		model?: string;
		features?: Record<string, unknown>;
	};
	if (
		!user ||
		position <= 1 ||
		fields.role !== 'user' ||
		!content.trim() ||
		page.messages.some((view) => !view.finished)
	)
		throw new Error('Reload this chat to continue');
	const answer = page.messages.find(
		(view) => view.position > position && (view.content as { role?: string })?.role === 'assistant'
	);
	return {
		at: position - 1,
		input: {
			content,
			files: fields.files,
			model: fields.model ?? (answer?.content as { model?: string })?.model ?? model,
			features: structuredClone(fields.features ?? features),
			edit_from: { thread_id: page.thread_id, position }
		}
	};
};
