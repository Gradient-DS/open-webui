import type { DisplayCitation } from '$lib/components/chat/Messages/Citations/reduceSources';
import { fileCitation } from '$lib/components/chat/Messages/Citations/fileCitation';
import type { ChatAttachment } from '$lib/types/chatAttachment';
import { derived, writable } from 'svelte/store';

// [Gradient] Sources tab opening is independent of its retained citation state.
export const openSourcesTabSignal = writable(0);
export const citationPanel = writable<null | {
	citation: DisplayCitation | null;
	level?: 'list' | 'detail';
	citations: DisplayCitation[];
	showPercentage: boolean;
	showRelevance: boolean;
	messageId: string;
	chatId: string;
	// [Gradient] A chat file opened from its chip, not tied to an answer's sources.
	file?: boolean;
}>(null);

// [Gradient] Show a chat file in the sources panel; the panel switches to its citations when an answer cites it.
export function openFileInPanel(chatId: string, item: ChatAttachment & { id: string }) {
	citationPanel.set({
		citation: fileCitation(item),
		level: 'detail',
		citations: [],
		showPercentage: false,
		showRelevance: false,
		messageId: '',
		chatId,
		file: true
	});
	openSourcesTabSignal.update((value) => value + 1);
}
// [Gradient] Whether the sources pane is on screen; the panel state outlives it so reopening resumes.
export const citationPaneVisible = writable(false);
// [Gradient] Inline badges share the cumulative index of the selected source, only while it is shown.
export const activeCitationIndex = derived(
	[citationPanel, citationPaneVisible],
	([panel, visible]) => {
		if (!visible || !panel?.citation || panel.level === 'list') return null;
		const index = panel.citations.indexOf(panel.citation) + 1;
		return index > 0 ? { messageId: panel.messageId, index } : null;
	}
);
