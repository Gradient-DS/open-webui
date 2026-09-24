import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const consumers = [
	'workspace/Knowledge/KnowledgeBase.svelte',
	'workspace/Knowledge/KnowledgeBase/Files.svelte',
	'workspace/Knowledge/KnowledgeBase/DirectoryRow.svelte',
	'workspace/Knowledge/KnowledgeBase/SourceRow.svelte',
	'workspace/Knowledge/KnowledgeBase/EmptyStateCards.svelte',
	'workspace/Knowledge/CreateKnowledgeBase.svelte',
	'workspace/Knowledge.svelte',
	'workspace/common/TypeSelector.svelte',
	'workspace/Models/Knowledge/KnowledgeSelector.svelte'
];

describe('a frontend provider needs only a registry entry and translations', () => {
	it.each(consumers)('%s has no provider special cases or environment gates', (file) => {
		const source = readFileSync(resolve('src/lib/components', file), 'utf8');
		expect(source).not.toMatch(/['"`]onedrive['"`]|['"`]google_drive['"`]/);
		expect(source).not.toContain('enable_onedrive_integration');
		expect(source).not.toContain('enable_google_drive_integration');
	});
});
