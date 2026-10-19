/**
 * [Gradient] A composer tool is on or off. On offers it to the model: web search, the document
 * writer, OneDrive search and mail search are then the model's to use (`auto` in the soev agent's
 * tool contract); image generation and Code Interpreter have no auto path and run on every message.
 *
 * OneDrive and mail search travel and persist as `'auto' | 'off'`, the others as booleans.
 */

export type LiveToolState = 'off' | 'auto';

/** A saved live tool state; Altijd, which earlier versions stored, reads as on. */
export function liveDocumentState(value: unknown): LiveToolState {
	return value === 'auto' || value === 'required' ? 'auto' : 'off';
}

/** i18n key for a tool's state. */
export const toolStateLabel = (on: boolean) => (on ? 'On' : 'Off');

/** i18n key for a tool that is off; an on tool uses its own description. */
export const TOOL_OFF_DESCRIPTION = 'Not used in this chat';
