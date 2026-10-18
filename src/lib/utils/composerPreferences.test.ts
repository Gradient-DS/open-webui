import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { composerPreferences, composerFeatures, composerFromFeatures } from './composerPreferences';

const chosen = composerPreferences({
	webSearchEnabled: true,
	webSearchRequired: true,
	liveDocumentsState: 'auto',
	liveMailState: 'auto',
	imageGenerationEnabled: true,
	codeInterpreterEnabled: true,
	documentWriterEnabled: true,
	documentWriterRequired: true,
	selectedToolIds: ['tool'],
	selectedSkillIds: ['skill'],
	selectedFilterIds: ['filter']
});

describe('composer preferences', () => {
	it.each(['Chat', 'Placeholder', 'MessageInput'])(
		'%s forwards every saved preference in both directions on every composer surface',
		(component) => {
			const source = readFileSync(`src/lib/components/chat/${component}.svelte`, 'utf8');
			const ast = parse(source);
			const composers: any[] = [];
			const visit = (node: any) => {
				if (!node || typeof node !== 'object') return;
				if (
					node.type === 'InlineComponent' &&
					['Placeholder', 'MessageInput', 'InputMenu'].includes(node.name)
				)
					composers.push(node);
				Object.values(node).forEach((value) => {
					if (Array.isArray(value)) value.forEach(visit);
					else visit(value);
				});
			};
			visit(ast.html);
			expect(composers.length).toBeGreaterThan(0);
			for (const composer of composers) {
				for (const key of Object.keys(chosen)) {
					expect(
						composer.attributes.find((attribute: any) => attribute.name === key),
						`${component} -> ${composer.name}: ${key}`
					).toMatchObject({ type: 'Binding', expression: { type: 'Identifier', name: key } });
				}
			}
			if (component !== 'Chat') {
				const exports = ast.instance?.content.body
					.filter((node: any) => node.type === 'ExportNamedDeclaration')
					.flatMap((node: any) => node.declaration?.declarations?.map((d: any) => d.id.name) ?? []);
				expect(exports).toEqual(expect.arrayContaining(Object.keys(chosen)));
			}
		}
	);
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
		expect(restored.liveMailState).toBe('off');
		expect(chosen.webSearchRequired).toBe(true);
	});
	it('partial preferences inherit missing choices and do not share selection arrays', () => {
		const restored = composerPreferences({ webSearchEnabled: false }, chosen);
		expect(restored.codeInterpreterEnabled).toBe(true);
		expect(restored.webSearchEnabled).toBe(false);
		restored.selectedToolIds.push('other');
		expect(chosen.selectedToolIds).toEqual(['tool']);
	});
});
