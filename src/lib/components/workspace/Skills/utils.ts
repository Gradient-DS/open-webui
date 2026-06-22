// Pure helpers for the skill-bundle folder tree. Kept free of Svelte/DOM
// dependencies so they can be unit-tested in isolation.

const MARKDOWN_EXTENSIONS = ['.md', '.markdown'];

/**
 * Return true if `name` (a filename or path) has a markdown extension.
 * Mirrors the server-side rule in routers/skill_files.py (.md / .markdown).
 */
export const isMarkdownPath = (name: string): boolean => {
	const lower = (name ?? '').toLowerCase();
	return MARKDOWN_EXTENSIONS.some((ext) => lower.endsWith(ext));
};

export interface SkillFileItem {
	id?: string;
	path: string;
	updated_at?: number;
	data?: { content?: string; [key: string]: unknown };
	meta?: { size?: number; [key: string]: unknown };
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

/**
 * Partition a list of files into the markdown ones we keep and a count of the
 * non-markdown ones we skip. Used by the upload + drag-drop paths.
 */
export const filterMarkdownFiles = (files: File[]): { kept: File[]; skipped: number } => {
	const kept: File[] = [];
	let skipped = 0;
	for (const file of files ?? []) {
		if (isMarkdownPath(file.name)) {
			kept.push(file);
		} else {
			skipped += 1;
		}
	}
	return { kept, skipped };
};
