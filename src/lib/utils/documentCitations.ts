import type { RawSource } from '$lib/components/chat/Messages/Citations/reduceSources';
import { normalizeCitations, buildFullSourceList, formatSourcesAsMarkdown } from './citations';

export function getExportMarkdown(content: string, sources: RawSource[], heading: string): string {
	if (sources.length === 0) return content;
	const normalized = normalizeCitations(content, sources);
	const body = normalized.content;
	const appendix = normalized.sourceList.length
		? normalized.sourceList
		: buildFullSourceList(sources);
	return appendix.length
		? `${body}\n\n---\n\n${formatSourcesAsMarkdown(appendix, heading)}\n`
		: content;
}
