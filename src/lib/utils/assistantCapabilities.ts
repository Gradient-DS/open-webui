/**
 * Maps the simple assistant builder's plain-language capability toggles
 * to/from the underlying Open WebUI `meta` fields.
 *
 * One toggle expands to up to three meta fields; `togglesFromMeta` is
 * its inverse. `applyToggles` preserves every meta field it does not
 * own, so the simple editor never clobbers advanced configuration.
 */

export interface AssistantToggles {
	web_search: boolean;
	image_generation: boolean;
	code_interpreter: boolean;
	document_writer: boolean;
	live_documents: boolean;
	live_mail: boolean;
	vision: boolean;
	file_upload: boolean;
	citations: boolean;
}

/** Toggles that are also Open WebUI "default features" (meta.defaultFeatureIds). */
export const FEATURE_TOGGLES = [
	'web_search',
	'image_generation',
	'code_interpreter',
	'document_writer',
	'live_documents',
	'live_mail'
] as const;

/** Tenant flags (`$config.features`) a capability needs before an editor offers it. */
const CAPABILITY_GATES: Record<string, string[]> = {
	memory: ['enable_memories'],
	web_search: ['enable_web_search'],
	image_generation: ['enable_image_generation'],
	code_interpreter: ['enable_code_interpreter'],
	document_writer: ['enable_document_writer', 'feature_document_writer'],
	live_documents: ['enable_live_documents'],
	live_mail: ['enable_live_mail'],
	builtin_tools: ['feature_builtin_tools']
};

/** Whether the tenant enables every flag the capability needs. */
export function capabilityOffered(capability: string, features: object | undefined): boolean {
	const flags = features as Record<string, unknown> | undefined;
	return (CAPABILITY_GATES[capability] ?? []).every((flag) => !!flags?.[flag]);
}

/** Capabilities the v2 agent never receives; the advanced editor still offers them. */
const AGENT_UNSERVED = ['image_generation', 'code_interpreter'];

/** Whether the simple builder offers the capability on this tenant. */
export function simpleCapabilityOffered(capability: string, features: object | undefined): boolean {
	const agentRouted = !!(features as Record<string, unknown> | undefined)
		?.feature_agent_api_enabled;
	if (agentRouted && AGENT_UNSERVED.includes(capability)) return false;
	return capabilityOffered(capability, features);
}

/** Toggles that are also built-in function-calling tools (meta.builtinTools). */
const BUILTIN_TOGGLES = ['web_search', 'image_generation', 'code_interpreter'] as const;

/** Derive the toggles from a model's meta object. */
export function togglesFromMeta(meta: any): AssistantToggles {
	const c = meta?.capabilities ?? {};
	return {
		web_search: !!c.web_search,
		image_generation: !!c.image_generation,
		code_interpreter: !!c.code_interpreter,
		document_writer: !!c.document_writer,
		live_documents: !!c.live_documents,
		live_mail: !!c.live_mail,
		vision: !!c.vision,
		file_upload: !!c.file_upload,
		citations: !!c.citations
	};
}

/**
 * Return a new meta object with the toggles applied.
 * Every field not owned by the toggles is carried through unchanged.
 */
export function applyToggles(meta: any, toggles: AssistantToggles): any {
	const capabilities = { ...(meta?.capabilities ?? {}) };
	capabilities.web_search = toggles.web_search;
	capabilities.image_generation = toggles.image_generation;
	capabilities.code_interpreter = toggles.code_interpreter;
	capabilities.document_writer = toggles.document_writer;
	capabilities.live_documents = toggles.live_documents;
	capabilities.live_mail = toggles.live_mail;
	capabilities.vision = toggles.vision;
	capabilities.file_upload = toggles.file_upload;
	capabilities.file_context = toggles.file_upload;
	capabilities.citations = toggles.citations;

	const otherFeatures = (meta?.defaultFeatureIds ?? []).filter(
		(id: string) => !FEATURE_TOGGLES.includes(id as (typeof FEATURE_TOGGLES)[number])
	);
	const enabledFeatures = FEATURE_TOGGLES.filter((id) => toggles[id]);
	const defaultFeatureIds = [...otherFeatures, ...enabledFeatures];

	const builtinTools = { ...(meta?.builtinTools ?? {}) };
	for (const id of BUILTIN_TOGGLES) {
		builtinTools[id] = toggles[id];
	}

	return { ...meta, capabilities, defaultFeatureIds, builtinTools };
}
