// [Gradient] Mentions and slash commands share the panel's assistant state and edit boundary.
import type { i18n as I18n } from 'i18next';
import { get } from 'svelte/store';
import { toast } from 'svelte-sonner';
import { models } from '$lib/stores';
import { activeAssistant, activeAssistantId } from '$lib/stores/assistant';
import { resolveAssistant } from './assistants';

export const selectAssistant = (query: string, editable: boolean, i18n: Pick<I18n, 't'>): void => {
	if (!query) {
		const assistant = get(activeAssistant);
		toast.message(
			assistant
				? i18n.t('Current assistant: {{name}}', { name: assistant.name })
				: i18n.t('No assistant selected')
		);
		return;
	}
	const assistant = resolveAssistant(query, get(models));
	if (!assistant) {
		toast.error(i18n.t('Assistant not found: {{name}}', { name: query }));
		return;
	}
	if (!editable) {
		toast.error(i18n.t('Start a new chat to switch assistants.'));
		return;
	}
	activeAssistantId.set(assistant.id);
};
