type SharingUser = {
	role?: string;
	permissions?: {
		chat?: { share?: boolean };
		sharing?: { public_chats?: boolean; open_chats?: boolean };
	};
};

/** Chat sharing follows the chat.share permission for every role; admins do not bypass it. */
export const chatSharing = (user: SharingUser | null | undefined) => {
	const share = user?.permissions?.chat?.share === true;
	return {
		share,
		public: share && user?.permissions?.sharing?.public_chats === true,
		open: share && user?.permissions?.sharing?.open_chats === true
	};
};
