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
	// Citation geometry: [{page?, x0, y0, x1, y1}] in PDF points, top-left
	// origin, 0-based pages — or that list as a JSON string (Weaviate MT
	// stores it as TEXT). Parsed by `$lib/utils/citationRects`.
	bboxes?: string | { page?: number; x0: number; y0: number; x1: number; y1: number }[];
	// Stamped per-chunk from the owning RawSource during the merge (a merged
	// document entry can mix chunks from a search citation and a read
	// citation of the same file): "document" = whole-document read, no
	// passage-level provenance — skip highlight attempts for this snippet.
	granularity?: string;
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
	// [Gradient] Per-source provenance from the agent service. `n` is the
	// cumulative `[N]` id; `current_turn` is true when a tool retrieved the
	// source this turn; `cited_this_turn` is true when the model wrote its
	// `[N]` in this turn's answer. Absent for legacy chats / upstream
	// providers. The per-message panel renders `current_turn ∪ cited_this_turn`.
	// `granularity` is "document" when the citation's contents are
	// whole-document reads (read_document/summarize/fetch_url) rather than
	// retrieved passages — the modal skips passage highlighting for those.
	// Absent (= chunk) for retrieval citations and all legacy payloads.
	granularity?: string;
	n?: number;
	current_turn?: boolean;
	cited_this_turn?: boolean;
}

export interface DisplayCitation {
	id: string;
	source: RawSourceObject;
	document: string[];
	metadata: RawSourceMeta[];
	// Parallel to `document`; `undefined` holes keep positions aligned for
	// chunks whose source carried no distances (e.g. whole-document reads).
	distances: (number | undefined)[];
	n?: number;
	current_turn?: boolean;
	cited_this_turn?: boolean;
}

export function reduceSources(sources: RawSource[]): DisplayCitation[] {
	const acc: DisplayCitation[] = [];
	const seenChunks = new Map<string, Set<string>>();

	for (const source of sources) {
		if (Object.keys(source).length === 0) continue;

		const documents = source?.document ?? [];

		documents.forEach((document, index) => {
			const rawMetadata = source?.metadata?.[index];
			// Stamp the owning source's granularity onto the chunk so it
			// survives the merge (entries can mix chunks from a search
			// citation and a read citation of the same document).
			const metadata: RawSourceMeta =
				source.granularity === 'document'
					? { ...(rawMetadata ?? {}), granularity: 'document' }
					: (rawMetadata ?? {});
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

			// [Gradient] Carry the agent's per-source provenance flags onto the
			// merged entry — they live at the top level of each `event: source`
			// payload, and this reducer rebuilds entries from scratch, so without
			// this they'd be dropped before the panel filter sees them. Re-applied
			// on every dispatch so the latest (post-answer) flags win:
			// `current_turn` / `cited_this_turn` flip as a turn progresses.
			if (source.n !== undefined) entry.n = source.n;
			if (source.current_turn !== undefined) entry.current_turn = source.current_turn;
			if (source.cited_this_turn !== undefined) entry.cited_this_turn = source.cited_this_turn;

			const seen = seenChunks.get(id)!;
			const fingerprint = chunkFingerprint(document, metadata);
			if (seen.has(fingerprint)) {
				return;
			}
			seen.add(fingerprint);

			// Push unconditionally: `document`, `metadata` and `distances` are
			// parallel arrays consumed positionally (CitationModal pairs
			// `metadata?.[i]` / `distances?.[i]` with `document[i]`) — a
			// conditional push desyncs every later index as soon as one chunk
			// lacks metadata or a distance (e.g. read-tool citations merged
			// into a searched document's entry).
			entry.document.push(document);
			entry.metadata.push(metadata);
			entry.distances.push(distance);
		});
	}

	return acc;
}

function chunkFingerprint(document: string, metadata: RawSourceMeta | undefined): string {
	const chunkId = metadata?.chunk_id;
	if (chunkId) return `id:${chunkId}`;
	const normalized = (document ?? '').split(/\s+/).filter(Boolean).join(' ').slice(0, 200);
	return `text:${normalized}`;
}
