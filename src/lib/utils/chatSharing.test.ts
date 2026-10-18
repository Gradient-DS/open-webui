import { describe, expect, it } from 'vitest';
import { chatSharing } from './chatSharing';

const withPermissions = (role: string, chat?: object, sharing?: object) => ({
	role,
	permissions: { chat, sharing }
});

describe('chatSharing', () => {
	it.each(['admin', 'user'])('denies %s without chat.share', (role) => {
		expect(chatSharing(withPermissions(role, { share: false }))).toEqual({
			share: false,
			public: false,
			open: false
		});
	});

	it.each(['admin', 'user'])('denies %s when permissions are missing', (role) => {
		expect(chatSharing({ role }).share).toBe(false);
		expect(chatSharing(withPermissions(role)).share).toBe(false);
	});

	it('denies a missing user', () => {
		expect(chatSharing(undefined).share).toBe(false);
	});

	it.each(['admin', 'user'])('allows %s with chat.share, without public or open grants', (role) => {
		expect(chatSharing(withPermissions(role, { share: true }, {}))).toEqual({
			share: true,
			public: false,
			open: false
		});
	});

	it.each(['admin', 'user'])('offers public and open grants to %s only when permitted', (role) => {
		const user = withPermissions(role, { share: true }, { public_chats: true, open_chats: true });
		expect(chatSharing(user)).toEqual({ share: true, public: true, open: true });
	});
});
