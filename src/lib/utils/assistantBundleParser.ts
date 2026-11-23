import JSZip from 'jszip';
import { parse } from 'yaml';

const SKILL_MD = 'SKILL.md';

export interface SkillBundleFile {
	path: string;
	blob: Blob;
}

export interface ParsedSkillBundle {
	skillMd: string;
	files: SkillBundleFile[];
}

// A bundle-relative path under a conventional skill subdir, e.g.
// "assets/template.docx". The negative lookbehind for \w / / keeps the match from
// firing inside a longer absolute path such as
// "/mnt/skills/public/docx/scripts/office/replace_text.py" (the "scripts/.." there
// is preceded by "/", so it is skipped) or an in-.docx path like "word/document.xml"
// (not under a skill subdir). Mirrors the soev-solutions check
// (deploy/skills/check_bundle_paths.py) so import-time and CI checks agree.
const REFERENCED_PATH_RE =
	/(?<![\w/])((?:assets|scripts|references|reference)\/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)/g;

// Return the bundle-relative paths a SKILL.md references that are NOT present in the
// bundle's files. Catches the flat-packaging bug where SKILL.md says
// "assets/template.docx" but the file was uploaded at the skill root. Heuristic and
// non-authoritative — intended to drive a non-blocking import warning.
export const findMissingSkillFiles = (skillMd: string, files: SkillBundleFile[]): string[] => {
	const present = new Set(files.map((f) => f.path));
	const referenced = new Set<string>();
	for (const match of skillMd.matchAll(REFERENCED_PATH_RE)) {
		referenced.add(match[1]);
	}
	return [...referenced].filter((path) => !present.has(path)).sort();
};

// Archive cruft we never want to import: macOS resource forks and Finder metadata.
const isMacJunk = (name: string): boolean =>
	name.startsWith('__MACOSX/') || name.split('/').pop() === '.DS_Store';

export const parseSkillBundle = async (file: File | Blob): Promise<ParsedSkillBundle> => {
	// Read the input into an ArrayBuffer before handing it to JSZip. JSZip's Blob
	// input path relies on FileReader, which doesn't exist outside the browser
	// (breaks unit tests); Blob.arrayBuffer() is universally available and avoids it.
	if (file.size > 25 * 1024 * 1024) throw new Error('BUNDLE_TOO_LARGE');
	const zip = await JSZip.loadAsync(await file.arrayBuffer());
	const entries = Object.values(zip.files).filter((entry) => !entry.dir && !isMacJunk(entry.name));

	// [Gradient] Accept a flat bundle or exactly one wrapping folder; never discard unrelated files.
	const roots = new Set(entries.map((entry) => entry.name.split('/')[0]));
	const prefix = zip.files[SKILL_MD] ? '' : roots.size === 1 ? `${[...roots][0]}/` : '';
	const skillMdEntry = zip.files[`${prefix}${SKILL_MD}`];
	if (!skillMdEntry || skillMdEntry.dir) throw new Error('NO_SKILL_MD');
	for (const entry of entries) {
		const original = (entry as typeof entry & { unsafeOriginalName?: string }).unsafeOriginalName;
		if (original && original !== entry.name) throw new Error('INVALID_PATH');
	}
	const skillMd = await skillMdEntry.async('string');

	const files: SkillBundleFile[] = [];
	for (const entry of Object.values(zip.files)) {
		if (entry.dir || entry === skillMdEntry || isMacJunk(entry.name)) continue;
		if (!entry.name.startsWith(prefix)) continue; // outside the bundle dir
		const path = entry.name.slice(prefix.length);
		if (!path) continue;
		const blob = await entry.async('blob');
		files.push({ path, blob });
	}
	return { skillMd, files };
};

export const buildSkillBundle = async (
	skillMd: string,
	files: SkillBundleFile[],
	rootDir = '',
	// [Gradient] Assistant exports omit directory entries and use the compressed-byte budget.
	options: { createFolders?: boolean; compression?: 'STORE' | 'DEFLATE' } = {}
): Promise<Blob> => {
	const zip = new JSZip();
	// Anthropic .skill bundles wrap their contents in a top-level <skill-name>/
	// directory. Pass rootDir to produce that layout so the bundle re-imports into
	// Claude; omit it (or pass '') for a flat bundle.
	const prefix = rootDir ? `${rootDir.replace(/\/+$/, '')}/` : '';
	zip.file(`${prefix}${SKILL_MD}`, skillMd, { createFolders: options.createFolders ?? true });
	// Convert each Blob to an ArrayBuffer first (see parseSkillBundle): JSZip reads
	// Blob inputs via FileReader, which is browser-only.
	for (const f of files)
		zip.file(`${prefix}${f.path}`, await f.blob.arrayBuffer(), {
			createFolders: options.createFolders ?? true
		});
	return zip.generateAsync({ type: 'blob', compression: options.compression ?? 'STORE' });
};

// [Gradient] Claude descriptions can be folded or multiline YAML scalars.
export const parseSkillMetadata = (content: string): { name?: string; description?: string } => {
	const match = content.match(/^---\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)/);
	if (!match) return {};
	const metadata: unknown = parse(match[1]);
	if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return {};
	const values = metadata as Record<string, unknown>;
	return {
		name: typeof values.name === 'string' ? values.name : undefined,
		description: typeof values.description === 'string' ? values.description : undefined
	};
};
