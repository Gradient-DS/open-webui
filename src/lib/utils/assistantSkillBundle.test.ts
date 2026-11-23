import { describe, expect, it, vi } from 'vitest';
import JSZip from 'jszip';
import { parse } from 'yaml';
import {
	assistantSkillName,
	buildAssistantSkill,
	parseAssistantSkill,
	restoreAssistantSettings,
	settingsForAssistantSkill,
	validateAssistantSkillFiles,
	validateAssistantSkillPath,
	droppedAssistantFiles
} from './assistantSkillBundle';
import { applyToggles, togglesFromMeta } from './assistantCapabilities';

const features = {
	enable_workspace: true,
	enable_web_search: true,
	enable_image_generation: true,
	enable_code_interpreter: true,
	enable_document_writer: true,
	feature_document_writer: true,
	enable_live_documents: true,
	enable_live_mail: true,
	enable_memories: true,
	feature_builtin_tools: true,
	enable_notes: true
};
const models = [{ id: 'invented-llm', info: { meta: { capabilities: { workspace: true } } } }];

describe('assistant skill round trip', () => {
	it.each([
		['Invented Tender Reviewer', ''],
		['Één invented helper 🤖', ''],
		['Invented Tender Reviewer', 'Invented description']
	])(
		'preserves display fields, bytes and settings for %s',
		async (name, description) => {
			const meta = applyToggles(
				{
					description,
					defaultModelId: 'invented-llm',
					knowledge: [
						{ type: 'collection', id: 'invented-kb' },
						{ type: 'note', id: 'invented-note' },
						{ type: 'folder', id: 'invented-folder' }
					],
					capabilities: { memory: true, status_updates: false },
					builtinTools: { time: false, notes: false }
				},
				{ ...togglesFromMeta({}), workspace: true, web_search: true, vision: true }
			);
			const assistant = {
				name,
				meta,
				params: { system: '\nInvented: exact instructions.\n---\n' }
			};
			const files = [
				{ path: 'scripts/run.py', blob: new Blob(['print("invented")\n']) },
				{ path: 'assets/template.bin', blob: new Blob([new Uint8Array([0, 255, 12])]) }
			];
			const blob = await buildAssistantSkill(assistant, files);
			const archive = await JSZip.loadAsync(await blob.arrayBuffer());
			const skillMd = await archive.file('SKILL.md')!.async('string');
			expect(parse(skillMd.slice(4, skillMd.indexOf('---\n', 4)))).toEqual({
				name: assistantSkillName(name),
				description: description || name
			});
			const imported = await parseAssistantSkill(new File([blob], 'invented.skill'));
			expect(imported.name).toBe(assistant.name);
			expect(imported.description).toBe(description);
			expect(imported.instructions).toBe(assistant.params.system);
			expect(imported.files.map((file) => file.path)).toEqual(files.map((file) => file.path));
			for (let index = 0; index < files.length; index++) {
				expect(await imported.files[index].blob.arrayBuffer()).toEqual(
					await files[index].blob.arrayBuffer()
				);
			}
			const restored = await restoreAssistantSettings(
				imported.settings,
				features,
				models,
				async () => true
			);
			expect(restored.omitted).toEqual([]);
			expect(restored.meta).toEqual(
				Object.fromEntries(Object.entries(meta).filter(([key]) => key !== 'description'))
			);
		}
	);

	it('imports a plain markdown skill with slug formatting, folded YAML and no frontmatter in the instructions', async () => {
		const imported = await parseAssistantSkill(
			new File(
				[
					'---\nname: tender-reviewer\ndescription: >-\n  Invented tender\n  reviewer\n---\nRead references/missing.md.\n'
				],
				'SKILL.md'
			)
		);
		expect(imported.name).toBe('Tender reviewer');
		expect(imported.description).toBe('Invented tender reviewer');
		expect(imported.instructions).toBe('Read references/missing.md.\n');
		expect(imported.missing).toEqual(['references/missing.md']);
	});

	it('imports a plain skill bundle without soev.json with a name derived from its slug', async () => {
		const archive = new JSZip()
			.file(
				'SKILL.md',
				'---\nname: tender-reviewer\ndescription: Review tenders\n---\nRead references/example.txt.\n'
			)
			.file('references/example.txt', 'Invented reference');
		const blob = await archive.generateAsync({ type: 'blob' });
		const imported = await parseAssistantSkill(new File([blob], 'external.skill'));
		expect(imported.name).toBe('Tender reviewer');
		expect(imported.description).toBe('Review tenders');
		expect(imported.instructions).toBe('Read references/example.txt.\n');
		expect(imported.files.map((file) => file.path)).toEqual(['references/example.txt']);
		expect(await imported.files[0].blob.text()).toBe('Invented reference');
		expect(imported.settings).toEqual({});
		expect(imported.missing).toEqual([]);
	});

	it('exports only reference metadata and no owner, grants or content', async () => {
		const blob = await buildAssistantSkill(
			{
				name: 'Invented',
				params: {},
				meta: {
					user_id: 'invented-user',
					access_grants: ['private'],
					secret: 'private',
					knowledge: [
						{ type: 'note', id: 'invented-note', content: 'private', user_id: 'invented-user' }
					]
				}
			},
			[]
		);
		const archive = await JSZip.loadAsync(await blob.arrayBuffer());
		const settings = JSON.parse(await archive.file('soev.json')!.async('string'));
		expect(Object.keys(settings)).toEqual([
			'version',
			'name',
			'description',
			'defaultModelId',
			'capabilities',
			'defaultFeatureIds',
			'builtinTools',
			'knowledge'
		]);
		expect(settings.knowledge).toEqual([{ type: 'note', id: 'invented-note' }]);
	});
});

describe('portable settings admission', () => {
	it('enables Workspace for plain skill files and preserves an exported off choice', async () => {
		expect(settingsForAssistantSkill({}, true, true)).toEqual({
			version: 1,
			capabilities: { workspace: true }
		});
		expect(settingsForAssistantSkill({}, true, false)).toEqual({});
		const meta = applyToggles({ description: 'Invented description' }, togglesFromMeta({}));
		const blob = await buildAssistantSkill({ name: 'Invented', meta, params: {} }, [
			{ path: 'scripts/invented.py', blob: new Blob(['print(1)']) }
		]);
		const imported = await parseAssistantSkill(new File([blob], 'invented.skill'));
		const restored = await restoreAssistantSettings(
			settingsForAssistantSkill(imported.settings, true, true),
			features,
			models,
			async () => true
		);
		expect(restored.meta.capabilities.workspace).toBe(false);
		expect(restored.meta.defaultFeatureIds).not.toContain('workspace');
	});
	it('drops files, unreadable references, unavailable toggles and an unpickable default', async () => {
		const canRead = vi.fn(async (_type, id) => id !== 'invented-private');
		const result = await restoreAssistantSettings(
			{
				version: 1,
				defaultModelId: 'invented-missing',
				capabilities: { workspace: true, web_search: true, unknown: true },
				knowledge: [
					{ type: 'file', id: 'invented-file' },
					{ type: 'note', id: 'invented-private' },
					{ type: 'folder', id: 'invented-readable' }
				],
				user_id: 'invented-other',
				unknown: 'ignored'
			},
			{},
			models,
			canRead
		);
		expect(result.meta.capabilities.workspace).toBe(false);
		expect(result.meta.capabilities.web_search).toBe(false);
		expect(result.meta.capabilities.unknown).toBeUndefined();
		expect(result.meta.knowledge).toEqual([{ type: 'folder', id: 'invented-readable' }]);
		expect(result.meta.defaultModelId).toBeUndefined();
		expect(result.omitted).toEqual([
			'workspace',
			'web_search',
			'invented-file',
			'invented-private',
			'invented-missing'
		]);
		expect(canRead).not.toHaveBeenCalledWith('file', 'invented-file');
	});

	it('does not restore a model lacking tool or workspace support', async () => {
		const result = await restoreAssistantSettings(
			{ version: 1, defaultModelId: 'invented-plain', capabilities: { workspace: true } },
			features,
			[{ id: 'invented-plain', info: { meta: { capabilities: { workspace: false } } } }],
			async () => true
		);
		expect(result.meta.defaultModelId).toBeUndefined();
		expect(result.omitted).toEqual(['invented-plain']);
	});

	it('ignores unknown settings versions and malformed optional JSON', async () => {
		expect(
			await restoreAssistantSettings(
				{ version: 99, capabilities: { workspace: true } },
				features,
				models,
				async () => true
			)
		).toEqual({ meta: {}, omitted: [] });
		const archive = new JSZip().file('SKILL.md', 'Invented body').file('soev.json', '{bad');
		const blob = await archive.generateAsync({ type: 'blob' });
		const imported = await parseAssistantSkill(new File([blob], 'invented.skill'));
		expect(imported.settings).toEqual({});
		expect(imported.files).toEqual([]);
	});
});

describe('folder paths and archive limits', () => {
	it.each([
		'',
		'/absolute',
		'../escape',
		'a/./b',
		'a//b',
		'a\\b',
		'C:/a',
		'SKILL.md',
		'soev.json',
		'x'.repeat(256)
	])('refuses %j', (path) => {
		expect(() => validateAssistantSkillPath(path)).toThrow('INVALID_PATH');
	});
	it('keeps slug output valid', () => {
		for (const name of ['___invented', 'Één 🤖', 'x'.repeat(200)])
			expect(assistantSkillName(name)).toMatch(/^[a-z0-9][a-z0-9_-]{0,63}$/);
	});
	it('counts case collisions, total bytes and generated entries', () => {
		expect(() =>
			validateAssistantSkillFiles([
				{ path: 'a', blob: new Blob() },
				{ path: 'A', blob: new Blob() }
			])
		).toThrow('INVALID_PATH');
		expect(() =>
			validateAssistantSkillFiles(
				Array.from({ length: 499 }, (_, index) => ({ path: `${index}`, blob: new Blob() }))
			)
		).toThrow('TOO_MANY_FILES');
		expect(() =>
			validateAssistantSkillFiles([{ path: 'a', blob: { size: 25 * 1024 * 1024 + 1 } as Blob }])
		).toThrow('BUNDLE_TOO_LARGE');
	});
	it('refuses Unix symlinks before expanding the archive', async () => {
		const archive = new JSZip()
			.file('SKILL.md', 'Invented body')
			.file('link', '../outside', { unixPermissions: 0o120777 });
		const blob = await archive.generateAsync({ type: 'blob', platform: 'UNIX' });
		await expect(parseAssistantSkill(new File([blob], 'invented.zip'))).rejects.toThrow(
			'INVALID_PATH'
		);
	});
	it('keeps dropped directory paths and reads all directory batches', async () => {
		const batches = [
			[
				{
					name: 'invented.txt',
					isFile: true,
					file: (resolve: (file: File) => void) => resolve(new File(['invented'], 'invented.txt'))
				}
			],
			[]
		];
		const reader = {
			readEntries: (resolve: (entries: unknown) => void) => resolve(batches.shift())
		};
		const folder = { name: 'references', isDirectory: true, createReader: () => reader };
		const result = await droppedAssistantFiles({
			items: [{ webkitGetAsEntry: () => folder }]
		} as unknown as DataTransfer);
		expect(result[0].path).toBe('references/invented.txt');
		expect(await result[0].blob.text()).toBe('invented');
	});
});
