import type { RawSource } from '$lib/components/chat/Messages/Citations/reduceSources';
import { normalizeCitations, buildFullSourceList, formatSourcesAsMarkdown } from './citations';

const CITATION = /\[(\d+(?:,\s*\d+)*)\]/g;

// `superscript` raises the body's [#] for print; the source list keeps plain [#] labels.
export function getExportMarkdown(
	content: string,
	sources: RawSource[],
	heading: string,
	superscript = false
): string {
	if (sources.length === 0) return content;
	const normalized = normalizeCitations(content, sources);
	const body = superscript
		? normalized.content.replace(CITATION, '<sup>[$1]</sup>')
		: normalized.content;
	const appendix = normalized.sourceList.length
		? normalized.sourceList
		: buildFullSourceList(sources);
	return appendix.length
		? `${body}\n\n---\n\n${formatSourcesAsMarkdown(appendix, heading)}\n`
		: content;
}
