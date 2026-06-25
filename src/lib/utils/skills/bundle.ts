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
	files: SkillBundleFile[]
): Promise<Blob> => {
	const zip = new JSZip();
	zip.file(SKILL_MD, skillMd);
	// Convert each Blob to an ArrayBuffer first (see parseSkillBundle): JSZip reads
	// Blob inputs via FileReader, which is browser-only.
	for (const f of files) zip.file(f.path, await f.blob.arrayBuffer());
	return zip.generateAsync({ type: 'blob' });
};
