import { get } from 'svelte/store';
import { config } from '$lib/stores';
import { DEFAULT_CAPABILITIES } from '$lib/constants';

/**
 * Returns DEFAULT_CAPABILITIES with disabled features set to false.
 * Used when initializing capabilities for new models.
 */
export function getDefaultCapabilities() {
	const $config = get(config);
	const features = $config?.features ?? {};
	return {
		...DEFAULT_CAPABILITIES,
		web_search: features.enable_web_search !== false ? DEFAULT_CAPABILITIES.web_search : false,
		image_generation:
			features.enable_image_generation !== false ? DEFAULT_CAPABILITIES.image_generation : false,
		code_interpreter:
			features.enable_code_interpreter !== false ? DEFAULT_CAPABILITIES.code_interpreter : false,
		builtin_tools:
			features.feature_builtin_tools !== false ? DEFAULT_CAPABILITIES.builtin_tools : false
	};
}
