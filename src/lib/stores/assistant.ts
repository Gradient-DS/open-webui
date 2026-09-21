// [Gradient] Selection is restored by each chat load, and cleared by a plain new chat.
import { derived, get, writable } from 'svelte/store';
import { config, models, settings } from '$lib/stores';
import {
	defaultLLMId,
	effectiveModel,
	isAssistant,
	isLLM,
	splitSelection
} from '$lib/utils/assistants';

const storageKey = 'activeAssistantId';
const readAssistantId = (): string | null => {
	if (typeof window === 'undefined') return null;
	try {
		return window.sessionStorage.getItem(storageKey) || null;
	} catch {
		return null;
	}
};

export const activeAssistantId = writable<string | null>(readAssistantId());
if (typeof window !== 'undefined') {
	activeAssistantId.subscribe((id) => {
		try {
			if (id) window.sessionStorage.setItem(storageKey, id);
			else window.sessionStorage.removeItem(storageKey);
		} catch {
			// Storage can be unavailable in private browsing.
		}
	});
}

export const activeAssistant = derived(
	[activeAssistantId, models],
	([$id, $models]) => $models.find((model) => model.id === $id && isAssistant(model)) ?? null
);

export const effectiveModels = derived([models, activeAssistant], ([$models, $assistant]) =>
	$assistant
		? $models.map((model) => (isLLM(model) ? effectiveModel(model, $assistant) : model))
		: $models
);

const split = (ids: string[]) => {
	const rawModels = get(models);
	const preferred = [
		...(get(settings)?.models ?? []),
		...(get(config)?.default_models ?? '').split(',')
	];
	return splitSelection(ids, rawModels, defaultLLMId(rawModels, preferred));
};

export const reconcileAssistantSelection = (
	ids: string[],
	urlAssistantId?: string | null,
	savedAssistantId?: string | null
): string[] => {
	const selection = split(ids);
	const id = savedAssistantId ?? urlAssistantId ?? selection.assistantId;
	activeAssistantId.set(id);
	return id ? selection.llmIds.slice(0, 1) : selection.llmIds;
};

// Selection-only paths (picker, folder updates, regeneration) must not rebind a saved chat.
export const llmSelection = (ids: string[]): string[] => {
	const { llmIds } = split(ids);
	return get(activeAssistantId) ? llmIds.slice(0, 1) : llmIds;
};

export const reconcileFolderSelection = (
	ids: string[],
	editable: boolean,
	urlAssistantId?: string | null
): string[] => (editable ? reconcileAssistantSelection(ids, urlAssistantId) : llmSelection(ids));
