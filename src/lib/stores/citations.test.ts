import { get } from 'svelte/store';
import { beforeEach, describe, expect, it } from 'vitest';
import {
	activeCitationIndex,
	citationPaneVisible,
	citationPanel,
	openFileInPanel,
	openSourcesTabSignal
} from './citations';

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

describe('openFileInPanel', () => {
	it('opens the file as a whole-document source and selects the Sources tab', () => {
		const signal = get(openSourcesTabSignal);
		openFileInPanel('c1', { type: 'file', id: 'f1', name: 'Handbook.docx' });
		const panel = get(citationPanel);
		expect(panel).toMatchObject({ chatId: 'c1', file: true, level: 'detail', messageId: '' });
		expect(panel?.citation?.metadata[0]).toMatchObject({ file_id: 'f1', granularity: 'document' });
		expect(get(openSourcesTabSignal)).toBe(signal + 1);
	});

	it('marks no inline citation badge', () => {
		citationPaneVisible.set(true);
		openFileInPanel('c1', { type: 'file', id: 'f1' });
		expect(get(activeCitationIndex)).toBeNull();
	});
});
