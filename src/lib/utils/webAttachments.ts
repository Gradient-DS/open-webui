/**
 * [Gradient] Routing for a URL the user attaches to a chat message (GRA-222).
 *
 * There are two ways a web page can reach the model, and they are not
 * interchangeable:
 *
 *  - **ingest** — the upstream path. `POST /process/web` (or `/process/youtube`)
 *    fetches the URL server-side, extracts text, embeds it into a Weaviate
 *    collection, and the chat sends a `type: 'text'` item carrying that
 *    `collection_name`. Retrieval then happens like it would for any uploaded
 *    file.
 *  - **agent** — the URL is passed through untouched as `{type: 'url', url,
 *    name}`. The agent service turns it into an attached source whose id IS the
 *    URL and reads the page live with its `fetch_url` tool. Nothing is stored.
 *
 * On agent-routed deployments the ingest path is a dead end: the agent's chat
 * translator only understands `file` / `collection` / `url` items, so a
 * `type: 'text'` item is dropped without a log and the model is told nothing
 * was attached — the GRA-222 bug. But the agent path cannot serve every URL
 * either, so this is a routing decision rather than a blanket switch:
 *
 *  - **YouTube** stays on ingest. `/process/youtube` pulls the *transcript* via
 *    youtube-transcript-api; the agent's fetcher would get the watch page's
 *    HTML, which contains nothing worth reading.
 *  - **Documents** (PDF, DOCX, XLSX, …) stay on ingest. `get_content_from_url`
 *    detects a non-text `Content-Type` and runs the file through the document
 *    loader; the agent's fetcher is an HTML extractor and does not do that.
 *
 * The document check is by URL extension because that is all the client can
 * see — the real signal is the `Content-Type` of a response nobody has made
 * yet. A document URL that hides its extension (`/download?id=123`) therefore
 * takes the agent path and the agent reports it could not read the page.
 */

import { isYoutubeUrl } from '$lib/utils';

export type WebAttachmentRoute = 'agent' | 'ingest';

/**
 * Extensions whose URLs must keep the ingest path: the server downloads them
 * and runs the document loader, which the agent's HTML fetcher cannot do.
 * Deliberately narrower than the loader's full list — only formats a link is
 * realistically pointed at.
 */
const DOCUMENT_URL_EXTENSIONS = new Set([
	'pdf',
	'doc',
	'docx',
	'xls',
	'xlsx',
	'ppt',
	'pptx',
	'odt',
	'ods',
	'odp',
	'rtf',
	'epub',
	'csv'
]);

/** Whether the URL's path names a document format the agent cannot fetch. */
export function isDocumentUrl(url: string): boolean {
	let pathname: string;
	try {
		pathname = new URL(url).pathname;
	} catch {
		return false;
	}
	const lastSegment = pathname.split('/').pop() ?? '';
	const dot = lastSegment.lastIndexOf('.');
	if (dot <= 0) {
		return false;
	}
	return DOCUMENT_URL_EXTENSIONS.has(lastSegment.slice(dot + 1).toLowerCase());
}

/**
 * Which path a newly attached URL should take.
 *
 * @param url - The URL the user attached.
 * @param agentRouted - Whether this chat is served by the agent API. When
 *   false the upstream ingest path is always used, so non-agent deployments
 *   behave exactly as they do today.
 */
export function routeWebAttachment(url: string, agentRouted: boolean): WebAttachmentRoute {
	if (!agentRouted) {
		return 'ingest';
	}
	if (isYoutubeUrl(url) || isDocumentUrl(url)) {
		return 'ingest';
	}
	return 'agent';
}
