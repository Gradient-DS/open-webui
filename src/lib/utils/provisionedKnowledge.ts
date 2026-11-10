import type { ProvisionedKnowledge } from '$lib/apis/provisionedKnowledge';

export const PROVISIONED_KNOWLEDGE_PIN_PREFIX = 'provisioned_knowledge:';

/** Opaque collection keys, each once in stored order. */
export function provisionedKnowledgeIds(value: unknown): string[] {
	return Array.isArray(value)
		? [...new Set(value.filter((id): id is string => typeof id === 'string' && id.length > 0))]
		: [];
}

/** The server rechecks provisioned status and access when the list is unavailable. */
export function requestedProvisionedKnowledge(
	selected: readonly string[],
	available: readonly ProvisionedKnowledge[] | null
): string[] {
	const ids = provisionedKnowledgeIds(selected);
	return available === null
		? ids
		: ids.filter((id) => available.some((source) => source.id === id));
}

/** Switches one provisioned collection on or off. */
export function toggleProvisionedKnowledge(selected: readonly string[], id: string): string[] {
	return selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id];
}
