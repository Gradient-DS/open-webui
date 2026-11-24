// [Gradient] Assistant identity and composition never change LLM dispatch fields.
export type AssistantModel = {
	id: string;
	name?: string;
	preset?: boolean;
	arena?: boolean;
	owned_by?: string;
	info?: {
		base_model_id?: string | null;
		meta?: {
			capabilities?: Record<string, unknown> | null;
			hidden?: boolean;
			defaultFeatureIds?: string[];
			defaultModelId?: string;
			toolIds?: string[];
			knowledge?: unknown[];
			soev?: { capabilities?: { tools?: string } | null } | null;
		};
		params?: object;
	};
};

type Capabilities = Record<string, unknown>;

export const isAssistant = (model?: AssistantModel | null): boolean =>
	model?.info?.base_model_id != null;

export const isLLM = (model?: AssistantModel | null): boolean =>
	!!model && !isAssistant(model) && !model.preset && !model.arena && model.owned_by !== 'arena';

export const resolveAssistant = <T extends AssistantModel>(
	query: string,
	models: T[]
): T | undefined => {
	const byId = models.find((model) => model.id === query);
	if (byId) return isAssistant(byId) ? byId : undefined;
	return models.find((model) => isAssistant(model) && model.name === query);
};

/**
 * Functionalities a user switches per chat. An assistant's choice for these is the starting
 * state of a chat with it (`defaultFeatureIds`), never a limit: only the LLM decides what can run.
 */
export const SWITCHABLE_CAPABILITIES = [
	'web_search',
	'image_generation',
	'code_interpreter',
	'document_writer',
	'workspace',
	'live_documents',
	'live_mail'
];

export const effectiveCapabilities = (
	llmCaps?: object | null,
	assistantCaps?: object | null
): Capabilities => {
	const base = (llmCaps ?? {}) as Capabilities;
	const override = Object.fromEntries(
		Object.entries((assistantCaps ?? {}) as Capabilities).filter(
			([key]) => !SWITCHABLE_CAPABILITIES.includes(key)
		)
	);
	return {
		...base,
		...override,
		workspace: base.workspace === true,
		vision: Boolean(
			(base.vision === undefined ? true : base.vision) &&
			(override.vision === undefined ? true : override.vision)
		)
	};
};

export const effectiveModel = <T extends AssistantModel | null | undefined>(
	llm: T,
	assistant?: AssistantModel | null
): T => {
	if (!assistant || !llm) return llm;
	const info = { ...llm.info };
	delete info.base_model_id;
	return {
		...llm,
		info: {
			...info,
			meta: {
				...llm.info?.meta,
				...assistant.info?.meta,
				capabilities: effectiveCapabilities(
					llm.info?.meta?.capabilities,
					assistant.info?.meta?.capabilities
				)
			},
			params: { ...llm.info?.params, ...assistant.info?.params }
		},
		assistant_id: assistant.id
	};
};

export const splitSelection = (
	ids: string[],
	models: AssistantModel[],
	fallbackLLMId: string
): { llmIds: string[]; assistantId: string | null } => {
	const byId = new Map(models.map((model) => [model.id, model]));
	const assistant = ids.map((id) => byId.get(id)).find(isAssistant);
	const hasLLM = ids.some((id) => isLLM(byId.get(id)));
	const fallback = isLLM(byId.get(fallbackLLMId))
		? fallbackLLMId
		: isLLM(byId.get(assistant?.info?.base_model_id ?? ''))
			? assistant!.info!.base_model_id!
			: '';
	let replaced = false;
	const llmIds = ids.flatMap((id) => {
		const model = byId.get(id);
		if (!model || isLLM(model)) return [id];
		if (!hasLLM && !replaced) {
			replaced = true;
			return [fallback];
		}
		return [];
	});
	return { llmIds: [...new Set(llmIds)], assistantId: assistant?.id ?? null };
};

export const defaultLLMId = (models: AssistantModel[], preferredIds: string[] = []): string =>
	preferredIds.find((id) => models.some((model) => model.id === id && isLLM(model))) ??
	models.find((model) => isLLM(model) && !model.info?.meta?.hidden)?.id ??
	'';

/** Functionalities the agent runs as tool calls. */
const TOOL_FEATURES = ['web_search', 'document_writer', 'workspace', 'live_documents', 'live_mail'];

/** Whether the assistant has anything the agent can only do with tool calls. */
export const usesTools = (assistant?: AssistantModel | null): boolean => {
	const meta = assistant?.info?.meta;
	return (
		(meta?.defaultFeatureIds ?? []).some((id) => TOOL_FEATURES.includes(id)) ||
		(meta?.toolIds ?? []).length > 0 ||
		(meta?.knowledge ?? []).length > 0
	);
};

/** The agent's own rule: only a model the catalog marks `unsupported` gets no tools. */
export const supportsTools = (model?: AssistantModel | null): boolean =>
	model?.info?.meta?.soev?.capabilities?.tools !== 'unsupported';

/** [Gradient] Assistants require the LLM to support their tools and workspace. */
export const pickableLLMs = <T extends AssistantModel>(
	models: T[],
	assistant?: AssistantModel | null
): T[] => {
	const llms = models.filter(isLLM);
	const needsWorkspace = assistant?.info?.meta?.capabilities?.workspace === true;
	return llms.filter(
		(model) =>
			(!(usesTools(assistant) || needsWorkspace) || supportsTools(model)) &&
			(!needsWorkspace || model.info?.meta?.capabilities?.workspace === true)
	);
};

/** The selected LLM, or the default pickable one when the assistant's tools rule it out. */
export const pickableLLMId = (
	id: string,
	models: AssistantModel[],
	assistant?: AssistantModel | null,
	preferredIds: string[] = []
): string => {
	const pickable = pickableLLMs(models, assistant);
	if (id && !isLLM(models.find((model) => model.id === id))) return id;
	if (pickable.some((model) => model.id === id)) return id;
	return defaultLLMId(pickable, preferredIds);
};

/**
 * Whether the URL's `model`/`models` parameter names an LLM. A link to an assistant alone
 * (`/?model=<assistant id>`, as the sidebar and the assistants list use) chooses no LLM.
 */
export const urlChoosesLLM = (params: URLSearchParams, models: AssistantModel[]): boolean =>
	(params.get('models') || params.get('model') || '')
		.split(',')
		.some((id) => isLLM(models.find((model) => model.id === id)));

// [Gradient] Only the initial assistant activation may choose an LLM for the chat.
export const assistantDefaultLLMId = (
	models: AssistantModel[],
	assistant: AssistantModel | null | undefined,
	{
		emptyChat,
		explicitModel,
		selectionLocked
	}: {
		emptyChat: boolean;
		explicitModel: boolean;
		selectionLocked: boolean;
	}
): string | undefined => {
	if (!emptyChat || explicitModel || selectionLocked) return undefined;
	const id = assistant?.info?.meta?.defaultModelId;
	return id && pickableLLMs(models, assistant).some((model) => model.id === id) ? id : undefined;
};
