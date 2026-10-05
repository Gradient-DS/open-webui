import { describe, expect, it } from 'vitest';
import { composerPreferences, composerFeatures, composerFromFeatures } from './composerPreferences';

const chosen = composerPreferences({
	webSearchEnabled: true,
	webSearchRequired: true,
	liveDocumentsState: 'auto',
	imageGenerationEnabled: true,
	codeInterpreterEnabled: true,
	documentWriterEnabled: true,
	selectedToolIds: ['tool'],
	selectedSkillIds: ['skill'],
	selectedFilterIds: ['filter']
});

describe('composer preferences', () => {
	it('round trips every setting through chat creation, another turn and reload', () => {
		const created = JSON.parse(JSON.stringify({ features: composerFeatures(chosen) }));
		const nextTurn = composerFromFeatures(created.features);
		expect(nextTurn).toEqual(chosen);
		expect(composerFromFeatures(composerFeatures(nextTurn))).toEqual(chosen);
	});
	it('new chats use last-used settings over model defaults, including explicit off and empty lists', () => {
		const lastUsed = composerPreferences();
		const user = JSON.parse(JSON.stringify({ ui: { composerTools: lastUsed } }));
		expect(composerPreferences(user.ui.composerTools, chosen)).toEqual(lastUsed);
	});
	it('chat settings are independent of later user defaults and old booleans restore as Auto', () => {
		const restored = composerFromFeatures({ web_search: true });
		expect(restored.webSearchEnabled).toBe(true);
		expect(restored.webSearchRequired).toBe(false);
		expect(restored.liveDocumentsState).toBe('off');
		expect(chosen.webSearchRequired).toBe(true);
	});
	it('partial drafts inherit missing choices and do not share selection arrays', () => {
		const restored = composerPreferences({ webSearchEnabled: false }, chosen);
		expect(restored.codeInterpreterEnabled).toBe(true);
		expect(restored.webSearchEnabled).toBe(false);
		restored.selectedToolIds.push('other');
		expect(chosen.selectedToolIds).toEqual(['tool']);
	});
});
