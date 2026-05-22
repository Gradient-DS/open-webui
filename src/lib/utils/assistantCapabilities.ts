/**
 * Maps the simple assistant builder's six plain-language capability
 * toggles to/from the underlying Open WebUI `meta` fields.
 *
 * One toggle expands to up to three meta fields; `togglesFromMeta` is
 * its inverse. `applyToggles` preserves every meta field it does not
 * own, so the simple editor never clobbers advanced configuration.
 */

export interface AssistantToggles {
	web_search: boolean;
	image_generation: boolean;
	code_interpreter: boolean;
	vision: boolean;
	file_upload: boolean;
	citations: boolean;
}

/** Toggles that also live in defaultFeatureIds + builtinTools. */
const FEATURE_TOGGLES = ['web_search', 'image_generation', 'code_interpreter'] as const;

/** Derive the six toggles from a model's meta object. */
export function togglesFromMeta(meta: any): AssistantToggles {
	const c = meta?.capabilities ?? {};
	return {
		web_search: !!c.web_search,
		image_generation: !!c.image_generation,
		code_interpreter: !!c.code_interpreter,
		vision: !!c.vision,
		file_upload: !!c.file_upload,
		citations: !!c.citations
	};
}

/**
 * Return a new meta object with the six toggles applied.
 * Every field not owned by the six toggles is carried through unchanged.
 */
export function applyToggles(meta: any, toggles: AssistantToggles): any {
	const capabilities = { ...(meta?.capabilities ?? {}) };
	capabilities.web_search = toggles.web_search;
	capabilities.image_generation = toggles.image_generation;
	capabilities.code_interpreter = toggles.code_interpreter;
	capabilities.vision = toggles.vision;
	capabilities.file_upload = toggles.file_upload;
	capabilities.file_context = toggles.file_upload;
	capabilities.citations = toggles.citations;

	const others = (meta?.defaultFeatureIds ?? []).filter(
		(id: string) => !FEATURE_TOGGLES.includes(id as (typeof FEATURE_TOGGLES)[number])
	);
	const enabledFeatures = FEATURE_TOGGLES.filter((id) => toggles[id]);
	const defaultFeatureIds = [...others, ...enabledFeatures];

	const builtinTools = { ...(meta?.builtinTools ?? {}) };
	builtinTools.web_search = toggles.web_search;
	builtinTools.image_generation = toggles.image_generation;
	builtinTools.code_interpreter = toggles.code_interpreter;

	return { ...meta, capabilities, defaultFeatureIds, builtinTools };
}
