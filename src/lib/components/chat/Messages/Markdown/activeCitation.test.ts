import { describe, expect, it } from 'vitest';
import { activeCitationIndexFor } from './activeCitation';

const panel = { chatId: 'chat-123', messageId: 'message-456' };
const active = { messageId: panel.messageId, index: 3 };

describe('activeCitationIndexFor', () => {
	it.each(['chat-123-message-456', 'chat-123-message-456-token-7'])(
		'finds the cumulative citation index for %s',
		(id) => expect(activeCitationIndexFor(id, panel, active)).toBe(3)
	);

	it.each(['', 'chat-123-message-4567', 'chat-123-message-457-token', 'chat-124-message-456'])(
		'does not highlight an unrelated markdown id %s',
		(id) => expect(activeCitationIndexFor(id, panel, active)).toBeNull()
	);

	it('clears the selection when the panel or active detail is absent', () => {
		expect(activeCitationIndexFor('chat-123-message-456', null, active)).toBeNull();
		expect(activeCitationIndexFor('chat-123-message-456', panel, null)).toBeNull();
	});

	it('ignores a selection retained from a different message', () => {
		expect(
			activeCitationIndexFor('chat-123-message-456', panel, { messageId: 'old', index: 3 })
		).toBeNull();
	});

	it('does not highlight a panel with no message id', () => {
		expect(
			activeCitationIndexFor('chat-123-', { ...panel, messageId: '' }, { messageId: '', index: 3 })
		).toBeNull();
	});
});
