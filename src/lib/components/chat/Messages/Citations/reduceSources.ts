/**
 * Pure reducer that turns the flat list of `event: source` SSE payloads
 * appended by `Chat.svelte` into the deduplicated citation list rendered
 * by `Citations.svelte`.
 *
 * The agent service (`soev_chat_manual` / ChatAgent) dispatches the
 * cumulative SourceCitation set after every tool call and every text
 * answer in a turn, so the same chunk can arrive multiple times in
 * `message.sources`. The backend runner now dedupes across dispatches
 * (genai-utils `CitationChunkDeduper`), but this reducer keeps the
 * defense in place for:
 *   - legacy chats persisted before the backend fix shipped,
 *   - other agent variants whose source emission path does not dedupe
 *     at the SSE boundary,
 *   - any upstream OpenWebUI provider that pushes overlapping source
 *     events into the same message.
 *
 * Dedup key: `chunk_id` from the chunk metadata when present, otherwise
 * a whitespace-normalised prefix of the chunk text. Scoped per merged
 * source entry — two documents with identical chunk text both render.
 */

export interface RawSourceMeta {
	source?: string;
	name?: string;
	file_id?: string;
	page?: number | string;
	chunk_id?: string;
	html?: string;
	parameters?: unknown;
	[key: string]: unknown;
}

export interface RawSourceObject {
	id?: string;
	name?: string;
	type?: string;
	url?: string;
	[key: string]: unknown;
}

export interface RawSource {
	source?: RawSourceObject;
	document?: string[];
	metadata?: RawSourceMeta[];
	distances?: number[];
}

export interface DisplayCitation {
	id: string;
	source: RawSourceObject;
	document: string[];
	metadata: RawSourceMeta[];
	distances: number[];
}

export function reduceSources(sources: RawSource[]): DisplayCitation[] {
	const acc: DisplayCitation[] = [];
	const seenChunks = new Map<string, Set<string>>();

	for (const source of sources) {
		if (Object.keys(source).length === 0) continue;

		const documents = source?.document ?? [];

		documents.forEach((document, index) => {
			const metadata = source?.metadata?.[index];
			const distance = source?.distances?.[index];

			const id = String(metadata?.source ?? source?.source?.id ?? 'N/A');
			let _source: RawSourceObject = source?.source ?? {};

			if (metadata?.name) {
				_source = { ..._source, name: metadata.name };
			}
			if (id.startsWith('http://') || id.startsWith('https://')) {
				_source = { ..._source, name: id, url: id };
			}

			let entry = acc.find((item) => item.id === id);
			if (!entry) {
				entry = {
					id,
					source: _source,
					document: [],
					metadata: [],
					distances: []
				};
				acc.push(entry);
				seenChunks.set(id, new Set());
			}

			const seen = seenChunks.get(id)!;
			const fingerprint = chunkFingerprint(document, metadata);
			if (seen.has(fingerprint)) {
				return;
			}
			seen.add(fingerprint);

			entry.document.push(document);
			if (metadata) entry.metadata.push(metadata);
			if (distance !== undefined) entry.distances.push(distance);
		});
	}

	return acc;
}

function chunkFingerprint(document: string, metadata: RawSourceMeta | undefined): string {
	const chunkId = metadata?.chunk_id;
	if (chunkId) return `id:${chunkId}`;
	const normalized = (document ?? '')
		.split(/\s+/)
		.filter(Boolean)
		.join(' ')
		.slice(0, 200);
	return `text:${normalized}`;
}
