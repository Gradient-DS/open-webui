type CitationMessage = { chatId: string; messageId: string };
type ActiveCitation = { messageId: string; index: number };

export function activeCitationIndexFor(
	id: string,
	panel: CitationMessage | null,
	active: ActiveCitation | null
): number | null {
	const messagePrefix = panel ? `${panel.chatId}-${panel.messageId}` : '';
	const messageId =
		messagePrefix && (id === messagePrefix || id?.startsWith(`${messagePrefix}-`))
			? panel?.messageId
			: null;
	return messageId && messageId === active?.messageId ? active.index : null;
}
