import { get } from 'svelte/store';
import { beforeEach, describe, expect, it } from 'vitest';
import { activeCitationIndex, citationPaneVisible, citationPanel } from './citations';

const citation = { source: { id: 'a' } } as never;

describe('activeCitationIndex', () => {
	beforeEach(() => {
		citationPanel.set({
			citation,
			level: 'detail',
			citations: [citation],
			showPercentage: false,
			showRelevance: false,
			messageId: 'm1',
			chatId: 'c1'
		});
	});

	it('marks the selected source while the sources pane is shown', () => {
		citationPaneVisible.set(true);
		expect(get(activeCitationIndex)).toEqual({ messageId: 'm1', index: 1 });
	});

	it('clears the mark when the pane closes, keeping the panel state', () => {
		citationPaneVisible.set(false);
		expect(get(activeCitationIndex)).toBeNull();
		expect(get(citationPanel)?.citation).toBe(citation);
	});
});
