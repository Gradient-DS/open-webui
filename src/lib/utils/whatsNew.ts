// [Gradient] "What's new" tour: one entry per minor soev release, its slides chosen per tenant and user.

export type WhatsNewSlide = 'auto_mode' | 'citations' | 'models' | 'outlook' | 'onedrive';

export type KnownModel = { match: RegExp; description: string };

export type Release = {
	/** major.minor: patch releases keep their minor's tour, so 1.3.1 does not show it again. */
	version: string;
	notesUrl: string;
	/** In order; each shows only when its gate (`SLIDE_GATES`) passes. */
	slides: WhatsNewSlide[];
	/** New models, announced by their provider/model id to users whose picker lists them unless the tenant names its own. */
	models: KnownModel[];
};

/** Add the next minor release at the end; the last entry is the tour users see. */
export const RELEASES: Release[] = [
	{
		version: '1.3',
		notesUrl: 'https://docs.soev.ai/docs/release-notes/v1.3.0',
		slides: ['auto_mode', 'citations', 'models', 'outlook', 'onedrive'],
		models: [
			{
				match: /glm-?5[.-]3(?!-?flash)/i,
				description: "Z.ai's newest, for complex questions"
			},
			{
				match: /glm-?5[.-]3-?flash/i,
				description: 'Fast answers to everyday questions'
			},
			{
				match: /mistral-large-4/i,
				description: 'The newest large model from Mistral AI'
			}
		]
	}
];

export const CURRENT_RELEASE = RELEASES[RELEASES.length - 1];

export type AnnouncedModel = { name: string; description?: string };

type Features = Record<string, unknown> | undefined;
type User = { role?: string; permissions?: { features?: Record<string, boolean> } } | undefined;
type PickerModel = {
	id: string;
	name?: string;
	info?: { base_model_id?: string | null; meta?: { hidden?: boolean } } | null;
};

/**
 * The release's new models among the user's visible base models, each by its provider/model id. A tenant's own
 * list (`WHATS_NEW_MODELS`, per release) replaces the release's.
 */
export function newModels(
	release: Release,
	models: PickerModel[] | undefined,
	configured?: Record<string, string[]>
): AnnouncedModel[] {
	const visible = (models ?? []).filter(
		(model) => !model.info?.base_model_id && !model.info?.meta?.hidden
	);
	const ids = configured?.[release.version];
	if (ids) {
		return ids
			.map((id) => visible.find((model) => model.id === id))
			.filter((model) => !!model)
			.map((model) => {
				const known = release.models.find((entry) => entry.match.test(model.id));
				return { name: model.id, description: known?.description };
			});
	}
	return release.models.flatMap(({ match, description }) => {
		const model = visible.find((candidate) => match.test(candidate.id));
		return model ? [{ name: model.id, description }] : [];
	});
}

const allowed = (user: User, permission: string) =>
	user?.role === 'admin' || !!user?.permissions?.features?.[permission];

/** Which composer tools have Auto for this user: the same gates as their composer buttons. */
export function autoModeTools(features: Features, user: User) {
	return {
		webSearch: !!features?.enable_web_search && allowed(user, 'web_search'),
		documentWriter:
			!!features?.enable_document_writer &&
			features?.feature_document_writer !== false &&
			allowed(user, 'document_writer')
	};
}

type SlideContext = { features: Features; user: User; models: AnnouncedModel[] };

const SLIDE_GATES: Record<WhatsNewSlide, (context: SlideContext) => boolean> = {
	auto_mode: ({ features, user }) => {
		const auto = autoModeTools(features, user);
		return auto.webSearch || auto.documentWriter;
	},
	citations: () => true,
	models: ({ models }) => models.length > 0,
	outlook: ({ features }) => !!features?.enable_live_mail,
	onedrive: ({ features }) => !!features?.enable_live_documents
};

export function whatsNewSlides(release: Release, context: SlideContext): WhatsNewSlide[] {
	return release.slides.filter((slide) => SLIDE_GATES[slide](context));
}

export function shouldShowWhatsNew(
	seen: string | undefined,
	user: User,
	release: Release = CURRENT_RELEASE
): boolean {
	return ['user', 'admin'].includes(user?.role ?? '') && seen !== release.version;
}
