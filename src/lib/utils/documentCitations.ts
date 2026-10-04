import type { RawSource } from '$lib/components/chat/Messages/Citations/reduceSources';
import { normalizeCitations, buildFullSourceList, formatSourcesAsMarkdown } from './citations';

// Same label selection as ContentRenderer's sourceIds and Source.svelte's pill.
export function documentSourceLabel(source: RawSource): string {
	const metadata = source.metadata?.[0];
	const id = metadata?.source ?? 'N/A';
	let label = metadata?.name ?? (/^https?:\/\//.test(id) ? id : (source.source?.name ?? id));
	try {
		label = decodeURIComponent(label);
	} catch {
		/* Keep a literal percent in source names. */
	}
	if (label.startsWith('http')) {
		label = label
			.replace('http://', '')
			.replace('https://', '')
			.split(/[/?#]/)[0]
			.replace(/^www\./, '');
	}
	return label.length > 30 ? label.slice(0, 15) + '...' + label.slice(-10) : label;
}

export function getExportMarkdown(content: string, sources: RawSource[], heading: string): string {
	if (sources.length === 0) return content;
	const normalized = normalizeCitations(content, sources);
	const appendix = normalized.sourceList.length
		? normalized.sourceList
		: buildFullSourceList(sources);
	return appendix.length
		? `${normalized.content}\n\n---\n\n${formatSourcesAsMarkdown(appendix, heading)}\n`
		: content;
}

const PILL_STYLE = `
button[data-document-citation] { font: 10px/1.5 Arial, sans-serif; display: inline-block; vertical-align: baseline; transform: translateY(2px); width: fit-content; padding: 2px 8px; border: 0; border-radius: 12px; background: #f9fafb; color: #000c; cursor: pointer; }
button[data-document-citation]:hover { color: #000; background: #f3f4f6; }
`;

// Only body text nodes participate: styles, attributes, titles and CSP stay untouched.
export function transformDocumentCitations(
	sanitized: string,
	sources: RawSource[],
	mode: 'preview' | 'print',
	heading = 'Sources'
): string {
	const doc = new DOMParser().parseFromString(sanitized, 'text/html');
	const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
	const nodes: Text[] = [];
	while (walker.nextNode()) {
		const node = walker.currentNode as Text;
		if (!node.parentElement?.closest('style, script, textarea')) nodes.push(node);
	}
	const numbers = new Map<number, number>();
	for (const node of nodes) {
		const fragment = doc.createDocumentFragment();
		const text = node.data;
		let end = 0;
		for (const match of text.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)) {
			fragment.append(text.slice(end, match.index));
			const ids = match[1].split(',').map(Number);
			if (mode === 'print') {
				fragment.append(
					'[' +
						ids
							.map((id) => {
								if (!sources[id - 1]) return id;
								if (!numbers.has(id)) numbers.set(id, numbers.size + 1);
								return numbers.get(id);
							})
							.join(', ') +
						']'
				);
			} else {
				ids.forEach((id, index) => {
					if (index) fragment.append(' ');
					const source = sources[id - 1];
					if (!source) {
						fragment.append(`[${id}]`);
						return;
					}
					const pill = doc.createElement('button');
					pill.type = 'button';
					pill.dataset.documentCitation = String(id);
					pill.textContent = documentSourceLabel(source);
					fragment.append(pill);
				});
			}
			end = match.index + match[0].length;
		}
		fragment.append(text.slice(end));
		node.replaceWith(fragment);
	}
	if (mode === 'preview') {
		const style = doc.createElement('style');
		style.textContent = PILL_STYLE;
		doc.head.append(style);
	} else if (numbers.size) {
		const section = doc.createElement('section');
		const title = doc.createElement('h2');
		title.textContent = heading;
		section.append(title);
		for (const [id, number] of numbers) {
			const source = sources[id - 1];
			const line = doc.createElement('p');
			const metadata = source.metadata?.[0];
			const url =
				source.source?.url || (/^https?:\/\//.test(metadata?.source ?? '') ? metadata?.source : '');
			const reference = url || metadata?.name || source.source?.name || metadata?.source || '';
			line.textContent = `[${number}] ${documentSourceLabel(source)}, ${reference}`;
			section.append(line);
		}
		doc.body.append(section);
	}
	return `<!doctype html>\n${doc.documentElement.outerHTML}`;
}

// The parent owns this listener; the frame needs no script permission.
export function bindDocumentCitationClicks(
	doc: Document,
	onClick: (id: number) => void
): () => void {
	const click = (event: Event) => {
		const target = event.target as Element | null;
		const pill = target?.closest?.('button[data-document-citation]');
		if (pill) onClick(Number(pill.getAttribute('data-document-citation')));
	};
	doc.addEventListener('click', click);
	return () => doc.removeEventListener('click', click);
}
