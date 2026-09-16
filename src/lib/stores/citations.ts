import type { DisplayCitation } from '$lib/components/chat/Messages/Citations/reduceSources';
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
}>(null);
// [Gradient] Inline badges share the cumulative index of the selected source.
export const activeCitationIndex = derived(citationPanel, (panel) => {
	if (!panel?.citation || panel.level === 'list') return null;
	const index = panel.citations.indexOf(panel.citation) + 1;
	return index > 0 ? { messageId: panel.messageId, index } : null;
});
