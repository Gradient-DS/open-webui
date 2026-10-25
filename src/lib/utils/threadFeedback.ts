import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ThreadRating = {
	thread_id: string;
	position: number;
	rating: -1 | 1;
	comment?: string;
};

export const submitThreadRating = async (token: string, rating: ThreadRating): Promise<void> => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/evaluations/feedback`, {
		method: 'POST',
		cache: 'no-store',
		headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
		body: JSON.stringify({
			thread_id: rating.thread_id,
			position: rating.position,
			rating: rating.rating,
			...(rating.comment ? { comment: rating.comment, comment_authored: true } : {})
		})
	});
	if (response.status !== 204) throw new Error('This chat is unavailable');
};

export type FeedbackItem = {
	thread_id: string;
	rating: -1 | 1;
	model: string;
	agent: string;
	time: string;
	comment: string | null;
};
export type FeedbackCount = Pick<FeedbackItem, 'model' | 'agent' | 'rating'> & { count: number };
export type FeedbackPage<T> = { items: T[]; next_cursor: string | null };

export const readThreadFeedback = async (
	token: string,
	options: { summary?: boolean; threadId?: string; cursor?: string } = {},
	signal?: AbortSignal
): Promise<FeedbackPage<FeedbackItem> | FeedbackPage<FeedbackCount>> => {
	const path = options.threadId
		? `/feedback/conversation/${encodeURIComponent(options.threadId)}`
		: options.summary
			? '/leaderboard'
			: '/feedbacks/list';
	const query = new URLSearchParams({ cursor: options.cursor ?? '', limit: '50' });
	const response = await fetch(`${WEBUI_API_BASE_URL}/evaluations${path}?${query}`, {
		cache: 'no-store',
		headers: { Authorization: `Bearer ${token}` },
		signal
	});
	if (!response.ok) throw new Error('This chat is unavailable');
	return response.json();
};
