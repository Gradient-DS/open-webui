import { liveDocumentState } from './toolState';

const featureKeys = {
	webSearchEnabled: 'web_search',
	liveDocumentsState: 'live_documents',
	liveMailState: 'live_mail',
	imageGenerationEnabled: 'image_generation',
	codeInterpreterEnabled: 'code_interpreter',
	documentWriterEnabled: 'document_writer',
	selectedToolIds: 'tool_ids',
	selectedSkillIds: 'skill_ids',
	selectedFilterIds: 'filter_ids'
} as const;

export type ComposerPreferences = {
	webSearchEnabled: boolean;
	liveDocumentsState: ReturnType<typeof liveDocumentState>;
	liveMailState: ReturnType<typeof liveDocumentState>;
	imageGenerationEnabled: boolean;
	codeInterpreterEnabled: boolean;
	documentWriterEnabled: boolean;
	selectedToolIds: string[];
	selectedSkillIds: string[];
	selectedFilterIds: string[];
};

export function composerPreferences(
	saved: Partial<ComposerPreferences> = {},
	defaults: Partial<ComposerPreferences> = {}
): ComposerPreferences {
	const values = { ...defaults, ...saved };
	return {
		webSearchEnabled: values.webSearchEnabled ?? false,
		liveDocumentsState: liveDocumentState(values.liveDocumentsState),
		liveMailState: liveDocumentState(values.liveMailState),
		imageGenerationEnabled: values.imageGenerationEnabled ?? false,
		codeInterpreterEnabled: values.codeInterpreterEnabled ?? false,
		documentWriterEnabled: values.documentWriterEnabled ?? false,
		selectedToolIds: [...(values.selectedToolIds ?? [])],
		selectedSkillIds: [...(values.selectedSkillIds ?? [])],
		selectedFilterIds: [...(values.selectedFilterIds ?? [])]
	};
}

export function composerFeatures(preferences: ComposerPreferences): Record<string, unknown> {
	return Object.fromEntries(
		Object.entries(featureKeys).map(([key, feature]) => [
			feature,
			preferences[key as keyof ComposerPreferences]
		])
	);
}

export function composerFromFeatures(
	features: Record<string, unknown> | null = {},
	defaults: Partial<ComposerPreferences> = {}
): ComposerPreferences {
	return composerPreferences(
		Object.fromEntries(
			Object.entries(featureKeys)
				.filter(([, feature]) => feature in (features ?? {}))
				.map(([key, feature]) => [key, features![feature]])
		),
		defaults
	);
}
