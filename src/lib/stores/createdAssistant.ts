// [Gradient] The assistant just created, handed to the edit page so it renders without a reload gap.
import { writable } from 'svelte/store';

export const createdAssistant = writable<Record<string, unknown> | null>(null);
