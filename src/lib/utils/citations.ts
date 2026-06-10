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

// Matches the citation forms recognized by citation-extension.ts so the
// downloaded document mirrors what the chat UI renders as badges:
//   - [N], [N, M]                — standard bracket citations
//   - [N#suffix]                 — standard brackets with hash-suffix targeting
//   - 【N】, 【N, M】              — Japanese fullwidth brackets
//   - 【N†meta】 (e.g. 【3†L1-L4】) — Japanese fullwidth with dagger metadata
// Capture group 1 holds the standard-bracket contents, group 2 the fullwidth contents.
const CITATION_RE =
	/\[(\d+(?:#[^,\]\s]+)?(?:\s*,\s*\d+(?:#[^,\]\s]+)?)*)\]|【(\d+(?:\s*,\s*\d+)*)(?:†[^】]*)?】/g;

// Parse "1, 2#foo, 3" → [1, 2, 3]. Strips any #suffix; numbers only.
function parseCitationGroup(group: string): number[] {
	return group
		.split(',')
		.map((part) => parseInt(part.trim().split('#')[0], 10))
		.filter((n) => !isNaN(n));
}

/**
 * Normalize citations in message content and build a source appendix.
 *
 * - Scans content for all supported citation marker forms (see CITATION_RE)
 * - Reorders sources by first appearance in text
 * - Returns normalized content (every marker rewritten to plain [N]) and source list
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
		const group = m[1] ?? m[2];
		if (!group) continue;
		for (const num of parseCitationGroup(group)) {
			if (!seen.has(num) && num >= 1 && num <= citations.length) {
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

	// 3. Replace every supported marker form with a renumbered [N] form
	const normalizedContent = content.replace(CITATION_RE, (match, std?: string, jp?: string) => {
		const group = std ?? jp;
		if (!group) return match;
		const renumbered = parseCitationGroup(group)
			.map((n) => renumberMap.get(n))
			.filter((n): n is number => n !== undefined);
		if (renumbered.length === 0) return match;
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
 * Build a full SourceInfo[] directly from raw sources, without scanning text
 * for [N] markers. Use when you want the same full source list the Citations
 * footer shows — i.e. regardless of whether the body text cites them inline.
 */
export function buildFullSourceList(sources: any[]): SourceInfo[] {
	const citations = reduceSources(sources);
	return citations.map((c, i) => ({
		index: i + 1,
		name: c.name,
		url: c.url
	}));
}

/**
 * [Gradient] Citation-identity helpers for the derived source panel.
 *
 * The agent service tags every `event: source` payload with its
 * cumulative `[N]` id (`n`) and turn provenance (`current_turn`). The
 * frontend derives the per-message panel from those fields plus the
 * `[N]` markers in the message content — chips and panel read the same
 * markers and the same source identities, so they cannot disagree.
 * Sources without `n` (vanilla OWUI RAG, third-party providers) keep
 * the legacy append/show-all path.
 */

/** A source payload is "tagged" when it carries a positive cumulative id. */
function isTaggedSource(source: any): boolean {
	return typeof source?.n === 'number' && source.n > 0;
}

/** Whether any source in the message carries citation identity. */
export function hasTaggedSources(sources: any[]): boolean {
	return (sources ?? []).some(isTaggedSource);
}

/**
 * Extract the set of cumulative `[N]` ids cited inline in `content`.
 *
 * Recognizes the same marker forms the chip renderer parses (see
 * CITATION_RE / citation-extension.ts): `[1]`, `[1, 2]`, `[4#suffix]`,
 * `【3】`, `【3†L1-L4】`.
 */
export function extractCitedNs(content: string): Set<number> {
	const cited = new Set<number>();
	if (!content) return cited;
	const scanRe = new RegExp(CITATION_RE.source, 'g');
	let m: RegExpExecArray | null;
	while ((m = scanRe.exec(content))) {
		const group = m[1] ?? m[2];
		if (!group) continue;
		for (const n of parseCitationGroup(group)) {
			cited.add(n);
		}
	}
	return cited;
}

/**
 * Derive the sources that belong in this message's bottom panel.
 *
 * Tagged sources show when retrieved by a tool this turn
 * (`current_turn`) or cited inline (`n` ∈ `citedNs`). Untagged sources
 * always show: when the whole message is untagged this is the legacy
 * show-all behavior, and in a mixed message they sit outside the
 * identity scheme so hiding them would silently drop citations.
 */
export function deriveVisibleSources(sources: any[], citedNs: Set<number>): any[] {
	return (sources ?? []).filter(
		(s) => !isTaggedSource(s) || s.current_turn === true || citedNs.has(s.n)
	);
}

/**
 * Build the chip display-name array for a tagged message, indexed by
 * cumulative id: position `n - 1` holds the source's display name.
 *
 * The inline chip renderer resolves `sourceIds[N - 1]`; for tagged
 * sources this array keys that lookup by identity instead of by the
 * deduped append order, so chips stay correct across re-dispatches and
 * cross-turn cites. Naming mirrors the legacy dense path: chunk
 * metadata name, else URL id, else the source object's name.
 */
export function buildTaggedSourceNames(sources: any[]): string[] {
	const names: string[] = [];
	for (const source of sources ?? []) {
		if (!isTaggedSource(source)) continue;
		const metadata = source?.metadata?.[0];
		const id: string = String(metadata?.source ?? source?.source?.id ?? 'N/A');
		let name: string;
		if (metadata?.name) {
			name = metadata.name;
		} else if (id.startsWith('http://') || id.startsWith('https://')) {
			name = id;
		} else {
			name = source?.source?.name ?? id;
		}
		names[source.n - 1] = name;
	}
	return names;
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
