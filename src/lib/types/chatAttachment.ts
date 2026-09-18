/** A chat attachment before or after its file has been uploaded. */
export interface ChatAttachment {
	type: string;
	id?: string | null;
	itemId?: string;
	name?: string;
	url?: string;
	path?: string;
	content_type?: string;
	status?: string;
	size?: number;
	error?: string;
	content?: string;
	knowledge_type?: string;
	file?: string | { data?: { content?: string }; [key: string]: unknown };
	[key: string]: unknown;
}

export interface ChatDraft {
	prompt: string;
	files: ChatAttachment[];
	selectedToolIds: string[];
	selectedSkillIds: string[];
	selectedFilterIds: string[];
	imageGenerationEnabled: boolean;
	webSearchEnabled: boolean;
	codeInterpreterEnabled: boolean;
	documentWriterEnabled: boolean;
	toolApprovalMode: string;
}

export interface ChatInputCallbacks {
	onUpload: (event: { type?: string; data?: unknown }) => void;
	onChange: (draft: ChatDraft) => void;
	onWebSearchToggle: (enabled: boolean) => void;
	createMessagePair: (prompt: string) => void;
	stopResponse: () => void;
	onToolApprovalModeChange: (mode: string) => void;
	oauthRedirectHandler: (
		tool: { id: string; serverId: string; authType?: string | null },
		draft: ChatDraft
	) => void;
}

export interface AskUserPrompt {
	show: boolean;
	questions: {
		id: string;
		header: string;
		question: string;
		options: { label: string; description: string }[];
		allow_other?: boolean;
	}[];
	allowOther: boolean;
	timeoutMs: number | null;
	onConfirm: (value: unknown) => void;
	onCancel: () => void;
}
