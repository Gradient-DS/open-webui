/**
 * Citation normalization and source appendix utilities.
 *
 * Shared by the per-message copy button and the whole-conversation copy.
 * Reuses the same citation marker patterns as citation-extension.ts.
 */

export interface SourceInfo {
	index: number; // 1-based, reordered by first appearance in text
	name: string;
	url?: string;
}

interface Citation {
	id: string;
	name: string;
	url?: string;
}

/**
 * Deduplicate raw source objects into a flat citation list,
 * using the same logic as Citations.svelte.
 */
function reduceSources(sources: any[]): Citation[] {
	const citations: Citation[] = [];

	for (const source of sources) {
		if (!source || Object.keys(source).length === 0) continue;

		const documents: any[] = source?.document ?? [];
		documents.forEach((_doc: any, idx: number) => {
			const metadata = source?.metadata?.[idx];
			const id: string = metadata?.source ?? source?.source?.id ?? 'N/A';
			let name: string = source?.source?.name ?? id;

			if (metadata?.name) {
				name = metadata.name;
			}

			let url: string | undefined;
			if (id.startsWith('http://') || id.startsWith('https://')) {
				name = id;
				url = id;
			}
			const sourceUrl = source?.source?.url ?? '';
			if (sourceUrl.startsWith('http://') || sourceUrl.startsWith('https://')) {
				url = sourceUrl;
			}

			if (!citations.find((c) => c.id === id)) {
				citations.push({ id, name: cleanSourceName(name), url });
			}
		});
	}

	return citations;
}

// Strip trailing UUID (v4-style) from source names, e.g.
// "Document.pdf - bccbdf8e-54c7-46b1-a542-f1163995a054" → "Document.pdf"
const TRAILING_UUID_RE =
	/\s*[-–—]\s*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function cleanSourceName(name: string): string {
	return name.replace(TRAILING_UUID_RE, '');
}

// Regex matching [N], [N,M], [N, M, ...] — standard bracket citations
const CITATION_RE = /\[(\d+(?:\s*,\s*\d+)*)\]/g;

/**
 * Normalize citations in message content and build a source appendix.
 *
 * - Scans content for [N] markers
 * - Reorders sources by first appearance in text
 * - Returns normalized content (with renumbered [N] markers) and source list
 */
export function normalizeCitations(
	content: string,
	sources: any[]
): { content: string; sourceList: SourceInfo[] } {
	const citations = reduceSources(sources);
	if (citations.length === 0) {
		return { content, sourceList: [] };
	}

	// 1. Collect all referenced original indices (1-based) in order of first appearance
	const seen = new Set<number>();
	const appearanceOrder: number[] = [];

	// Use a temporary regex scan to find first-appearance order
	let m: RegExpExecArray | null;
	const scanRe = new RegExp(CITATION_RE.source, 'g');
	while ((m = scanRe.exec(content))) {
		const nums = m[1].split(',').map((n) => parseInt(n.trim(), 10));
		for (const num of nums) {
			if (!isNaN(num) && !seen.has(num) && num >= 1 && num <= citations.length) {
				seen.add(num);
				appearanceOrder.push(num);
			}
		}
	}

	if (appearanceOrder.length === 0) {
		return { content, sourceList: [] };
	}

	// 2. Build mapping: original 1-based index → new sequential 1-based index
	const renumberMap = new Map<number, number>();
	appearanceOrder.forEach((orig, i) => {
		renumberMap.set(orig, i + 1);
	});

	// 3. Replace citations in content with renumbered versions
	const normalizedContent = content.replace(CITATION_RE, (_match, group: string) => {
		const nums = group.split(',').map((n: string) => parseInt(n.trim(), 10));
		const renumbered = nums
			.map((n) => renumberMap.get(n))
			.filter((n): n is number => n !== undefined);
		if (renumbered.length === 0) return _match;
		return '[' + renumbered.join(', ') + ']';
	});

	// 4. Build source list in appearance order
	const sourceList: SourceInfo[] = appearanceOrder.map((orig, i) => {
		const citation = citations[orig - 1]; // citations array is 0-based
		return {
			index: i + 1,
			name: citation?.name ?? `Source ${orig}`,
			url: citation?.url
		};
	});

	return { content: normalizedContent, sourceList };
}

/**
 * Format source list as markdown for plain text clipboard.
 */
export function formatSourcesAsMarkdown(sources: SourceInfo[]): string {
	if (sources.length === 0) return '';

	const lines = sources.map((s) => {
		const urlPart = s.url && s.url !== s.name ? ` - ${s.url}` : '';
		return `[${s.index}] ${s.name}${urlPart}`;
	});

	return `Bronnen:\n${lines.join('\n')}`;
}

/**
 * Format source list as HTML for rich clipboard.
 */
export function formatSourcesAsHtml(sources: SourceInfo[]): string {
	if (sources.length === 0) return '';

	const items = sources
		.map((s) => {
			const urlPart =
				s.url && s.url !== s.name
					? ` — <span style="color:#0066cc;word-break:break-all;">${s.url}</span>`
					: '';
			return `<div style="margin-bottom:4px;font-size:10pt;"><strong style="color:#0066cc;">[${s.index}]</strong> ${s.name}${urlPart}</div>`;
		})
		.join('\n');

	return `<div style="margin-top:16px;padding-top:8px;border-top:1px solid #ccc;"><h3 style="font-size:14pt;margin-bottom:8px;">Bronnen</h3>\n${items}</div>`;
}
