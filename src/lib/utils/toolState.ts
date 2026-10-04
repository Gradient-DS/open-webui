/**
 * [Gradient] Per-chat state of a composer tool, mirroring the soev agent's tool contract
 * (`off` = not offered, `auto` = offered for the model to decide, `required` = must use).
 *
 * Web search has all three. Image generation, Code Interpreter and Document Writer have
 * no real auto path, so they cycle between `off` and `required` only.
 *
 * Web search travels as two booleans: `webSearchEnabled` (auto or required, i.e. web
 * search is possible) and `webSearchRequired` (required).
 */

export type ToolState = 'off' | 'auto' | 'required';

/** Click order for web search, starting from the new-chat default. */
export const WEB_SEARCH_STATES: ToolState[] = ['auto', 'required', 'off'];

/** Click order for tools without an auto path. */
export const BINARY_TOOL_STATES: ToolState[] = ['off', 'required'];

/** The state after one click; an unknown state starts the cycle. */
export function nextToolState(state: ToolState, states: ToolState[]): ToolState {
	const index = states.indexOf(state);
	return states[(index + 1) % states.length];
}

export function webSearchState(enabled: boolean, required: boolean): ToolState {
	if (!enabled) return 'off';
	return required ? 'required' : 'auto';
}

export function webSearchFlags(state: ToolState): { enabled: boolean; required: boolean } {
	return { enabled: state !== 'off', required: state === 'required' };
}

/** i18n key for the state's own label. */
export const TOOL_STATE_LABELS: Record<ToolState, string> = {
	off: 'Off',
	auto: 'Auto',
	required: 'Always'
};

/** i18n key explaining what a web search state does. */
export const WEB_SEARCH_STATE_DESCRIPTIONS: Record<ToolState, string> = {
	off: 'Does not search the web',
	auto: 'The model decides whether to search the web',
	required: 'Searches the web for every message'
};

/** i18n key for a two-state tool that is off; the on state uses the tool's own description. */
export const TOOL_OFF_DESCRIPTION = 'Not used in this chat';

export const LIVE_DOCUMENT_STATES: ToolState[] = ['off', 'auto', 'required'];

export function liveDocumentState(value: unknown): ToolState {
	return value === 'auto' || value === 'required' ? value : 'off';
}
