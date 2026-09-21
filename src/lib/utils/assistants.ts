// [Gradient] Assistant identity and composition never change LLM dispatch fields.
export type AssistantModel = {
	id: string;
	name?: string;
	preset?: boolean;
	arena?: boolean;
	owned_by?: string;
	info?: {
		base_model_id?: string | null;
		meta?: { capabilities?: object | null; hidden?: boolean };
		params?: object;
	};
};

type Capabilities = Record<string, unknown>;

export const isAssistant = (model?: AssistantModel | null): boolean =>
	model?.info?.base_model_id != null;

export const isLLM = (model?: AssistantModel | null): boolean =>
	!!model && !isAssistant(model) && !model.preset && !model.arena && model.owned_by !== 'arena';

export const effectiveCapabilities = (
	llmCaps?: object | null,
	assistantCaps?: object | null
): Capabilities => {
	const base = (llmCaps ?? {}) as Capabilities;
	const override = (assistantCaps ?? {}) as Capabilities;
	return {
		...base,
		...override,
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
