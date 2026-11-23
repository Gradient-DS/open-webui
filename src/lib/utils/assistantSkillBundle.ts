// [Gradient] Portable assistant skill folders.
import JSZip from 'jszip';
import { DEFAULT_CAPABILITIES } from '$lib/constants';
import { stringify } from 'yaml';
import {
	buildSkillBundle,
	findMissingSkillFiles,
	parseSkillBundle,
	parseSkillMetadata,
	type SkillBundleFile
} from './assistantBundleParser';
import {
	applyToggles,
	simpleCapabilityOffered,
	togglesFromMeta,
	type AssistantToggles
} from './assistantCapabilities';
import { pickableLLMs, type AssistantModel } from './assistants';

export const MAX_SKILL_BYTES = 25 * 1024 * 1024;
const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)/;

export type AssistantSkillImport = {
	name: string;
	description: string;
	instructions: string;
	files: SkillBundleFile[];
	settings: Record<string, unknown>;
	missing: string[];
};

export const assistantSkillName = (name: string): string =>
	name
		.toLowerCase()
		.replace(/[^a-z0-9_-]+/g, '-')
		.replace(/^[-_]+|[-_]+$/g, '')
		.slice(0, 64) || 'assistant';

export const validateAssistantSkillPath = (path: string): void => {
	if (
		!path ||
		path.length > 255 ||
		/[\\:\x00-\x1f]/.test(path) ||
		path.split('/').some((part) => !part || part === '.' || part === '..') ||
		['skill.md', 'soev.json'].includes(path.toLowerCase())
	)
		throw new Error('INVALID_PATH');
};

export const validateAssistantSkillFiles = (files: SkillBundleFile[]): void => {
	if (files.length > 498) throw new Error('TOO_MANY_FILES');
	if (files.reduce((total, file) => total + file.blob.size, 0) > MAX_SKILL_BYTES)
		throw new Error('BUNDLE_TOO_LARGE');
	const seen = new Set<string>();
	for (const file of files) {
		validateAssistantSkillPath(file.path);
		const path = file.path.toLowerCase();
		if (seen.has(path)) throw new Error('INVALID_PATH');
		seen.add(path);
	}
};

export const parseAssistantSkill = async (file: File): Promise<AssistantSkillImport> => {
	if (file.size > MAX_SKILL_BYTES) throw new Error('BUNDLE_TOO_LARGE');
	let skillMd: string;
	let files: SkillBundleFile[];
	if (/\.md$/i.test(file.name)) {
		skillMd = await file.text();
		files = [];
	} else {
		// Check zip metadata before the shared parser expands entries, including Unix symlinks.
		const archive = await JSZip.loadAsync(await file.arrayBuffer());
		const entries = Object.values(archive.files);
		if (entries.length > 500) throw new Error('TOO_MANY_FILES');
		let expanded = 0;
		for (const entry of entries) {
			const permissions = Number(entry.unixPermissions ?? 0);
			if ((permissions & 0o170000) === 0o120000) throw new Error('INVALID_PATH');
			expanded +=
				(entry as typeof entry & { _data?: { uncompressedSize?: number } })._data
					?.uncompressedSize ?? 0;
			if (expanded > 100 * 1024 * 1024) throw new Error('BUNDLE_TOO_LARGE');
		}
		({ skillMd, files } = await parseSkillBundle(file));
	}
	const match = skillMd.match(FRONTMATTER);
	const metadata = parseSkillMetadata(skillMd);
	const slug = metadata.name || file.name.replace(/\.(skill|zip|md)$/i, '');
	const label = slug.replace(/[-_]+/g, ' ');
	const settingsFile = files.find((file) => file.path === 'soev.json');
	let settings: Record<string, unknown> = {};
	if (settingsFile) {
		try {
			const value = JSON.parse(await settingsFile.blob.text());
			if (value && value.version === 1) settings = value;
		} catch {
			/* Optional portable settings never prevent importing a plain skill. */
		}
	}
	files = files.filter((file) => file !== settingsFile);
	validateAssistantSkillFiles(files);
	return {
		name:
			typeof settings.name === 'string'
				? settings.name
				: label.charAt(0).toUpperCase() + label.slice(1),
		description:
			typeof settings.description === 'string' ? settings.description : metadata.description || '',
		instructions: match ? skillMd.slice(match[0].length) : skillMd,
		files,
		settings,
		missing: findMissingSkillFiles(skillMd, files)
	};
};

export const assistantSkillSettings = ({
	name,
	meta
}: {
	name: string;
	meta: any;
}): Record<string, unknown> => ({
	version: 1,
	name,
	description: meta.description || '',
	defaultModelId: meta.defaultModelId ?? null,
	capabilities: meta.capabilities ?? null,
	defaultFeatureIds: meta.defaultFeatureIds ?? null,
	builtinTools: meta.builtinTools ?? null,
	knowledge: (meta.knowledge ?? [])
		.filter((item: any) => typeof item?.type === 'string' && typeof item?.id === 'string')
		.map(({ type, id }: { type: string; id: string }) => ({ type, id }))
});

// Plain skills with files start with Workspace on. An exported assistant keeps an explicit off choice too.
export const settingsForAssistantSkill = (
	settings: Record<string, unknown>,
	hasFiles: boolean,
	workspaceOffered: boolean
): Record<string, unknown> => {
	const capabilities =
		settings.capabilities &&
		typeof settings.capabilities === 'object' &&
		!Array.isArray(settings.capabilities)
			? (settings.capabilities as Record<string, unknown>)
			: {};
	if (!hasFiles || !workspaceOffered || typeof capabilities.workspace === 'boolean')
		return settings;
	return { ...settings, version: 1, capabilities: { ...capabilities, workspace: true } };
};

export const buildAssistantSkill = async (
	assistant: { name: string; meta: any; params: { system?: string } },
	files: SkillBundleFile[]
): Promise<Blob> => {
	validateAssistantSkillFiles(files);
	const description = assistant.meta.description || assistant.name;
	if (!description || description.length > 1024) throw new Error('INVALID_DESCRIPTION');
	const skillMd = `---\n${stringify({
		name: assistantSkillName(assistant.name),
		description
	})}---\n${assistant.params.system || ''}`;
	const bundle = await buildSkillBundle(
		skillMd,
		[
			...files,
			{
				path: 'soev.json',
				blob: new Blob([JSON.stringify(assistantSkillSettings(assistant))])
			}
		],
		'',
		{ createFolders: false, compression: 'DEFLATE' }
	);
	if (bundle.size > MAX_SKILL_BYTES) throw new Error('BUNDLE_TOO_LARGE');
	return bundle;
};

export const restoreAssistantSettings = async (
	settings: Record<string, unknown>,
	features: object | undefined,
	models: AssistantModel[],
	canRead: (type: string, id: string) => Promise<boolean>
): Promise<{ meta: any; omitted: string[] }> => {
	if (settings.version !== 1) return { meta: {}, omitted: [] };
	const omitted: string[] = [];
	const capabilities =
		settings.capabilities &&
		typeof settings.capabilities === 'object' &&
		!Array.isArray(settings.capabilities)
			? (settings.capabilities as Record<string, unknown>)
			: {};
	const allowedCapabilities: Record<string, boolean> = {};
	const knownCapabilities = new Set([
		...Object.keys(DEFAULT_CAPABILITIES),
		'live_documents',
		'live_mail'
	]);
	for (const [key, value] of Object.entries(capabilities)) {
		if (!knownCapabilities.has(key) || typeof value !== 'boolean') continue;
		if (simpleCapabilityOffered(key, features)) allowedCapabilities[key] = value;
		else if (value) omitted.push(key);
	}
	const toggles = togglesFromMeta({ capabilities: allowedCapabilities });
	for (const key of Object.keys(toggles) as (keyof AssistantToggles)[]) {
		if (!simpleCapabilityOffered(key, features)) {
			toggles[key] = false;
		}
	}
	// Only known settings are restored; imported grants, owners and arbitrary metadata are ignored.
	const toolGates: Record<string, string> = {
		memory: 'enable_memories',
		notes: 'enable_notes',
		channels: 'enable_channels',
		knowledge: 'feature_knowledge'
	};
	const knownTools = new Set([
		'time',
		'user_input',
		'memory',
		'chats',
		'notes',
		'knowledge',
		'files',
		'channels',
		'notifications',
		'web_search',
		'image_generation',
		'code_interpreter',
		'tasks',
		'automations',
		'calendar'
	]);
	const builtinTools: Record<string, boolean> = {};
	if (settings.builtinTools && typeof settings.builtinTools === 'object') {
		for (const [key, value] of Object.entries(settings.builtinTools)) {
			if (!knownTools.has(key) || typeof value !== 'boolean') continue;
			if (
				simpleCapabilityOffered(key, features) &&
				(!toolGates[key] || (features as Record<string, unknown>)?.[toolGates[key]])
			)
				builtinTools[key] = value;
			else if (value) omitted.push(key);
		}
	}
	const defaultFeatureIds = Array.isArray(settings.defaultFeatureIds)
		? settings.defaultFeatureIds.filter(
				(key) =>
					typeof key === 'string' &&
					knownCapabilities.has(key) &&
					simpleCapabilityOffered(key, features)
			)
		: [];
	const meta = applyToggles(
		{ capabilities: allowedCapabilities, builtinTools, defaultFeatureIds },
		toggles
	);
	const knowledge = Array.isArray(settings.knowledge) ? settings.knowledge : [];
	meta.knowledge = [];
	for (const entry of knowledge) {
		if (
			entry &&
			typeof entry.type === 'string' &&
			typeof entry.id === 'string' &&
			['collection', 'note', 'folder'].includes(entry.type) &&
			(await canRead(entry.type, entry.id))
		) {
			meta.knowledge.push({ type: entry.type, id: entry.id });
		} else if (entry && typeof entry.id === 'string') omitted.push(entry.id);
	}
	if (typeof settings.defaultModelId === 'string' && settings.defaultModelId) {
		if (
			pickableLLMs(models, {
				id: 'imported-assistant',
				info: { base_model_id: 'assistant', meta }
			}).some((model) => model.id === settings.defaultModelId)
		)
			meta.defaultModelId = settings.defaultModelId;
		else omitted.push(settings.defaultModelId);
	}
	return { meta, omitted };
};

// Folder entries arrive in batches; readEntries must be called until it returns an empty batch.
export const droppedAssistantFiles = async (transfer: DataTransfer): Promise<SkillBundleFile[]> => {
	const walk = async (entry: FileSystemEntry, prefix = ''): Promise<SkillBundleFile[]> => {
		if (entry.isFile) {
			const file = await new Promise<File>((resolve, reject) =>
				(entry as FileSystemFileEntry).file(resolve, reject)
			);
			return [{ path: prefix + entry.name, blob: file }];
		}
		const reader = (entry as FileSystemDirectoryEntry).createReader();
		const result: SkillBundleFile[] = [];
		while (true) {
			const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
				reader.readEntries(resolve, reject)
			);
			if (!batch.length) break;
			for (const child of batch) result.push(...(await walk(child, `${prefix}${entry.name}/`)));
		}
		return result;
	};
	const entries = [...transfer.items]
		.map((item) => item.webkitGetAsEntry?.())
		.filter(Boolean) as FileSystemEntry[];
	if (!entries.length)
		return [...transfer.files].map((file) => ({
			path: file.webkitRelativePath || file.name,
			blob: file
		}));
	return (await Promise.all(entries.map((entry) => walk(entry)))).flat();
};
