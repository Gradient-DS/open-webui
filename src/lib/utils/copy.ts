/**
 * Shared utility for copying an entire chat conversation with rich formatting and sources.
 * Used by both sidebar ChatMenu and navbar Menu.
 */

import { createMessagesList, copyToClipboard, removeAllDetails } from '$lib/utils';

export async function copyFormattedChat(chat: any): Promise<boolean> {
	const history = chat.chat.history;
	const messages = createMessagesList(history, history.currentId);

	// Collect all sources across all assistant messages
	const allSources: any[] = [];

	// Build conversation text, collecting sources
	let conversationText = '';
	for (const message of messages) {
		const content = removeAllDetails(message.content);
		conversationText += `### ${message.role.toUpperCase()}\n${content}\n\n`;
		if (message.role === 'assistant' && message.sources?.length) {
			allSources.push(...message.sources);
		}
	}

	return await copyToClipboard(conversationText.trim(), null, true, allSources);
}
