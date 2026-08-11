import { describe, it, expect } from 'vitest';
import { classifyFileItem, getLiveSide, getHistorySide, getActiveSide } from './dataSeparation';

describe('classifyFileItem', () => {
	it('classifies web search and webpage URLs as open_internet', () => {
		expect(classifyFileItem({ type: 'web_search' })).toBe('open_internet');
		expect(classifyFileItem({ type: 'text', url: 'https://x.com' })).toBe('open_internet');
	});
	it('classifies an agent url attachment as open_internet', () => {
		// GRA-222: a page attached for the agent to fetch live is still the
		// open internet, even though nothing is ingested.
		expect(classifyFileItem({ type: 'url', url: 'https://x.com' })).toBe('open_internet');
	});
	it('classifies a bare text item (no url) as neither', () => {
		expect(classifyFileItem({ type: 'text' })).toBeNull();
	});
	it('classifies document sources as internal', () => {
		for (const t of ['file', 'image', 'collection', 'folder', 'chat', 'note']) {
			expect(classifyFileItem({ type: t })).toBe('internal');
		}
	});
	it('returns null for unknown / garbage', () => {
		expect(classifyFileItem({ type: 'mystery' })).toBeNull();
		expect(classifyFileItem(null)).toBeNull();
		expect(classifyFileItem(undefined)).toBeNull();
	});
});

describe('getLiveSide', () => {
	it('is open_internet when web search is on', () => {
		expect(getLiveSide([], true)).toBe('open_internet');
	});
	it('is internal when an internal file is attached', () => {
		expect(getLiveSide([{ type: 'collection' }], false)).toBe('internal');
	});
	it('is null when nothing is selected', () => {
		expect(getLiveSide([], false)).toBeNull();
		expect(getLiveSide(undefined, false)).toBeNull();
	});
});

describe('getHistorySide', () => {
	it('locks to the side used in prior messages', () => {
		const messages = [{ role: 'user', files: [{ type: 'file' }] }];
		expect(getHistorySide(messages)).toBe('internal');
	});
	it('detects open_internet webpage URLs in history', () => {
		const messages = [{ role: 'user', files: [{ type: 'text', url: 'u' }] }];
		expect(getHistorySide(messages)).toBe('open_internet');
	});
	it('is null for an empty / file-less history', () => {
		expect(getHistorySide([])).toBeNull();
		expect(getHistorySide([{ role: 'user' }])).toBeNull();
	});
});

describe('getActiveSide', () => {
	it('history side wins over live composition', () => {
		const messages = [{ role: 'user', files: [{ type: 'file' }] }];
		// even though web search is toggled on now, the conversation is locked internal
		expect(getActiveSide({ messages, files: [], webSearchEnabled: true })).toBe('internal');
	});
	it('falls back to live side on the first message', () => {
		expect(getActiveSide({ messages: [], files: [], webSearchEnabled: true })).toBe(
			'open_internet'
		);
	});
	it('is null when nothing is committed or selected', () => {
		expect(getActiveSide({ messages: [], files: [], webSearchEnabled: false })).toBeNull();
	});
});
