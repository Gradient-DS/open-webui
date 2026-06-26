import JSZip from 'jszip';

const SKILL_MD = 'SKILL.md';

// Extensions read as UTF-8 text and stored inline. Everything else is uploaded
// as a binary File. Mirrors the backend _TEXT_EXTENSIONS allowlist
// (backend/open_webui/utils/skill_bundles.py); when unsure, treat as binary
// (reading binary as UTF-8 would corrupt it; storing text as a File is harmless).
const TEXT_EXTENSIONS = new Set([
	'md', 'markdown', 'txt', 'py', 'js', 'ts', 'jsx', 'tsx', 'json', 'yaml', 'yml',
	'toml', 'ini', 'cfg', 'conf', 'sh', 'bash', 'zsh', 'html', 'htm', 'css', 'scss',
	'sass', 'xml', 'csv', 'rst', 'tex', 'sql', 'r', 'rb', 'java', 'c', 'cpp', 'h',
	'hpp', 'cs', 'go', 'rs', 'swift', 'kt', 'php', 'lua', 'tf', 'hcl'
]);

export interface SkillBundleFile {
	path: string;
	blob: Blob;
}

export interface ParsedSkillBundle {
	skillMd: string;
	files: SkillBundleFile[];
}

export const isTextPath = (path: string): boolean => {
	const ext = path.includes('.') ? path.split('.').pop()!.toLowerCase() : '';
	return TEXT_EXTENSIONS.has(ext);
};

// A bundle-relative path under a conventional skill subdir, e.g.
// "assets/template.docx". The negative lookbehind for \w / / keeps the match from
// firing inside a longer absolute path such as
// "/mnt/skills/public/docx/scripts/office/replace_text.py" (the "scripts/.." there
// is preceded by "/", so it is skipped) or an in-.docx path like "word/document.xml"
// (not under a skill subdir). Mirrors the genai-utils check
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
	const zip = await JSZip.loadAsync(await file.arrayBuffer());

	// Anthropic .skill bundles wrap their contents in a top-level <skill-name>/
	// directory, so SKILL.md is usually nested (e.g. "intermax-docx/SKILL.md")
	// rather than at the zip root. Find SKILL.md anywhere, prefer the shallowest
	// match, and treat its directory as the bundle root.
	const isSkillMd = (name: string) => name === SKILL_MD || name.endsWith(`/${SKILL_MD}`);
	const skillMdEntry = Object.values(zip.files)
		.filter((e) => !e.dir && !isMacJunk(e.name) && isSkillMd(e.name))
		.sort((a, b) => a.name.length - b.name.length)[0];
	if (!skillMdEntry) {
		throw new Error('NO_SKILL_MD');
	}
	const skillMd = await skillMdEntry.async('string');

	// Strip the SKILL.md's directory prefix so paths are relative to the skill root
	// (matching what buildSkillBundle writes). "" for a flat bundle.
	const prefix = skillMdEntry.name.slice(0, skillMdEntry.name.length - SKILL_MD.length);

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
	rootDir = ''
): Promise<Blob> => {
	const zip = new JSZip();
	// Anthropic .skill bundles wrap their contents in a top-level <skill-name>/
	// directory. Pass rootDir to produce that layout so the bundle re-imports into
	// Claude; omit it (or pass '') for a flat bundle.
	const prefix = rootDir ? `${rootDir.replace(/\/+$/, '')}/` : '';
	zip.file(`${prefix}${SKILL_MD}`, skillMd);
	// Convert each Blob to an ArrayBuffer first (see parseSkillBundle): JSZip reads
	// Blob inputs via FileReader, which is browser-only.
	for (const f of files) zip.file(`${prefix}${f.path}`, await f.blob.arrayBuffer());
	return zip.generateAsync({ type: 'blob' });
};
