// Pure helpers for the skill-bundle folder tree. Kept free of Svelte/DOM
// dependencies so they can be unit-tested in isolation.

/**
 * Extensions we treat as editable text in the right-hand pane. This set is kept
 * in lockstep with the backend's `_TEXT_EXTENSIONS`
 * (backend/open_webui/routers/skill_files.py + utils/skill_bundles.py) so the
 * client's editor-vs-preview decision matches the server's inline-vs-metadata
 * forwarding classification.
 */
const TEXT_EXTENSIONS = new Set<string>([
	'md',
	'markdown',
	'txt',
	'py',
	'js',
	'ts',
	'jsx',
	'tsx',
	'json',
	'yaml',
	'yml',
	'toml',
	'ini',
	'cfg',
	'conf',
	'sh',
	'bash',
	'zsh',
	'html',
	'htm',
	'css',
	'scss',
	'sass',
	'xml',
	'csv',
	'rst',
	'tex',
	'sql',
	'r',
	'rb',
	'java',
	'c',
	'cpp',
	'h',
	'hpp',
	'cs',
	'go',
	'rs',
	'swift',
	'kt',
	'php',
	'lua',
	'tf',
	'hcl'
]);

/**
 * Map a text extension to a CodeMirror `@codemirror/language-data` alias that
 * CodeEditor resolves via `languages.find((l) => l.alias.includes(lang))`. An
 * empty string falls back to plaintext (no language). Extensions absent here
 * (e.g. plain `.txt`, `.csv`) deliberately resolve to plaintext.
 */
const LANGUAGE_BY_EXTENSION: Record<string, string> = {
	py: 'python',
	js: 'javascript',
	jsx: 'javascript',
	ts: 'typescript',
	tsx: 'typescript',
	json: 'json',
	yaml: 'yaml',
	yml: 'yaml',
	toml: 'toml',
	md: 'markdown',
	markdown: 'markdown',
	html: 'html',
	htm: 'html',
	css: 'css',
	scss: 'css',
	sass: 'css',
	xml: 'xml',
	sh: 'shell',
	bash: 'shell',
	zsh: 'shell',
	sql: 'sql',
	java: 'java',
	c: 'c',
	cpp: 'cpp',
	h: 'c',
	hpp: 'cpp',
	cs: 'c#',
	go: 'go',
	rs: 'rust',
	rb: 'ruby',
	php: 'php',
	swift: 'swift',
	kt: 'kotlin',
	lua: 'lua',
	r: 'r'
};

/** Lower-cased extension (without the dot) of a filename or path, or '' if none. */
const extensionOf = (name: string): string => {
	const base = (name ?? '').toLowerCase().split('/').pop() ?? '';
	const dot = base.lastIndexOf('.');
	return dot > 0 ? base.slice(dot + 1) : '';
};

/**
 * Return true if `name` (a filename or path) should be edited as text rather
 * than previewed/downloaded as a binary. Aligned with the backend text
 * classification.
 */
export const isTextPath = (name: string): boolean => TEXT_EXTENSIONS.has(extensionOf(name));

/**
 * Map `name` to a CodeMirror language alias for the editor, or '' (plaintext)
 * when the extension is unknown or absent.
 */
export const languageForPath = (name: string): string =>
	LANGUAGE_BY_EXTENSION[extensionOf(name)] ?? '';

export interface SkillFileItem {
	id?: string;
	path: string;
	updated_at?: number;
	data?: { content?: string; [key: string]: unknown };
	meta?: { size?: number; [key: string]: unknown };
	media_type?: string;
	size?: number;
	[key: string]: unknown;
}

export interface SkillTreeFile extends SkillFileItem {
	name: string;
}

export interface SkillTreeNode {
	name: string;
	path: string; // folder prefix without a trailing slash, '' for root
	children: SkillTreeNode[];
	files: SkillTreeFile[];
	pending?: boolean; // client-only folder with no persisted files yet
}

const emptyNode = (name: string, path: string): SkillTreeNode => ({
	name,
	path,
	children: [],
	files: []
});

const sortNode = (node: SkillTreeNode): void => {
	node.children.sort((a, b) => a.name.localeCompare(b.name));
	node.files.sort((a, b) => a.name.localeCompare(b.name));
	for (const child of node.children) {
		sortNode(child);
	}
};

/**
 * Build a nested folder tree from a flat list of items that each carry a
 * POSIX-style `path` (e.g. `docs/sub/guide.md`). Folders are *implied* by the
 * path segments — there is no folder entity. `pendingFolders` are client-only
 * folder prefixes (e.g. from "New folder") that should appear even though no
 * file lives under them yet; a pending folder that collides with a real one is
 * dropped (the real folder wins).
 *
 * Folders are sorted before files, each alphabetically.
 */
export const buildTree = (items: SkillFileItem[], pendingFolders: string[] = []): SkillTreeNode => {
	const root = emptyNode('', '');

	const ensureFolder = (parent: SkillTreeNode, name: string): SkillTreeNode => {
		let child = parent.children.find((c) => c.name === name);
		if (!child) {
			const childPath = parent.path ? `${parent.path}/${name}` : name;
			child = emptyNode(name, childPath);
			parent.children.push(child);
		}
		return child;
	};

	for (const item of items ?? []) {
		const segments = (item.path ?? '').split('/').filter((s) => s !== '');
		if (segments.length === 0) continue;

		const fileName = segments[segments.length - 1];
		const folderSegments = segments.slice(0, -1);

		let cursor = root;
		for (const segment of folderSegments) {
			cursor = ensureFolder(cursor, segment);
		}
		cursor.files.push({ ...item, name: fileName });
	}

	for (const prefix of pendingFolders ?? []) {
		const segments = prefix.split('/').filter((s) => s !== '');
		if (segments.length === 0) continue;

		let cursor = root;
		for (const segment of segments) {
			const existedBefore = cursor.children.some((c) => c.name === segment);
			cursor = ensureFolder(cursor, segment);
			// Only flag as pending when we just created an otherwise-empty node.
			if (!existedBefore) {
				cursor.pending = true;
			}
		}
	}

	sortNode(root);
	return root;
};
