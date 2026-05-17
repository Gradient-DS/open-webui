// AUTO-GENERATED FILE — do not edit by hand.
// Regenerate with: npm run generate:ui-schemas
// Source schema: choice.schema.json

export interface ChoiceProps {
	/** Include an inline freetext input alongside the buttons. Turn off only when the option set is genuinely closed. */
	allow_freetext?: boolean;
	/** Labels the answer on return. Use when emitting multiple blocks in one message so each answer is self-identifying. */
	field?: string | null;
	/** Stable slug for this question within the message, e.g. 'kb-scope', 'summary-style'. Required. */
	id: string;
	/** Checkboxes (multi-select) instead of single-select buttons. */
	multi?: boolean;
	/** 2-6 short, mutually distinct labels for the user to pick between. */
	options: Array<string>;
	/** Question rendered above the buttons. Optional. */
	question?: string | null;
	/** Add a 'skip' button with this label. */
	skip_label?: string | null;
}
