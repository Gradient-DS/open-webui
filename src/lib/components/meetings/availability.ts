// [Gradient] Vergadering is offered only when the tenant flag is on and soev-api lists the meeting agent.
import { writable } from 'svelte/store';

import { getMeetingAgents } from '$lib/apis/meetings';
import { isFeatureEnabled } from '$lib/utils/features';
import { hasMeetingAgent } from './meeting';

export const meetingsAvailable = writable<boolean>(false);

let pending: Promise<boolean> | null = null;

/** Checks once per page load; any failure (soev-api down, agent not allowlisted) hides the entry. */
export const checkMeetingsAvailable = (token: string): Promise<boolean> => {
	if (!isFeatureEnabled('meetings')) {
		meetingsAvailable.set(false);
		return Promise.resolve(false);
	}
	pending ??= getMeetingAgents(token)
		.then((res) => hasMeetingAgent(res?.data))
		.catch(() => false)
		.then((available) => {
			meetingsAvailable.set(available);
			if (!available) pending = null;
			return available;
		});
	return pending;
};
