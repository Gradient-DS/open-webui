// A string that can either be a plain value (legacy) or a per-locale mapping.
// Resolve with `resolveLocalized()` from `$lib/utils/localized` before display.
export type LocalizedString = string | Record<string, string>;

export type Banner = {
	i18n?: Record<string, { content: string }>;
	id: string;
	type: string;
	title?: string;
	content: LocalizedString;
	url?: string;
	dismissible?: boolean;
	timestamp: number;
};

export enum TTS_RESPONSE_SPLIT {
	PUNCTUATION = 'punctuation',
	PARAGRAPHS = 'paragraphs',
	NONE = 'none'
}

export type TerminalContext = {
	terminalId: string | null;
	currentDirectory: string;
	file?: import('$lib/apis/terminal').FilesystemFile | null;
};
