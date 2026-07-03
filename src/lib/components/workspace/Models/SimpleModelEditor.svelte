<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import { config, models, user } from '$lib/stores';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import {
		togglesFromMeta,
		applyToggles,
		type AssistantToggles
	} from '$lib/utils/assistantCapabilities';

	import Knowledge from './Knowledge.svelte';
	import CapabilityToggles from './Simple/CapabilityToggles.svelte';
	import Selector from '$lib/components/chat/ModelSelector/Selector.svelte';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import Cog6 from '$lib/components/icons/Cog6.svelte';

	const i18n = getContext('i18n');

	// model = existing model when editing; null when creating fresh.
	export let model: any = null;
	// draft = AssistantDraft from the onboarding agent; null when not from the wizard.
	export let draft: any = null;
	export let edit = false;
	// options.skipNavigate lets callers persist without the parent route
	// navigating away — used by the "+ Add knowledge" flow, which saves
	// the assistant then navigates to the KB-create flow itself.
	export let onSubmit: (
		info: any,
		options?: { skipNavigate?: boolean }
	) => Promise<void> | void;
	export let onAdvanced: () => void;

	let loaded = false;
	let loading = false;
	// Snapshot of the field values at "saved" baseline. After onMount
	// it's the just-loaded edit / draft values; after a successful save
	// it's the values that were just persisted. isDirty diffs the live
	// snapshot against this baseline to drive the Save button.
	let savedSnapshot: string | null = null;
	// Whether the assistant has been saved at least once in this
	// session — drives Share button visibility (we don't want Share to
	// appear before there's anything to share).
	let hasBeenSaved = false;
	// True from the moment we kick off the auto-save until the parent
	// navigates away. Prevents the auto-save from firing twice.
	let autoSaving = false;

	// Edited-in-the-simple-view fields:
	let id = '';
	let name = '';
	let description = '';
	let system = '';
	let profileImageUrl = `${WEBUI_BASE_URL}/static/favicon.png`;
	let knowledge: any[] = [];
	let toggles: AssistantToggles = togglesFromMeta({});
	let accessGrants: any[] = [];
	let knowledgeHint = '';
	// The selected base model id. Bound to the Model picker; seeded at
	// mount from DEFAULT_MODELS (create) or the saved base model (edit).
	let baseModelId = '';

	// The full original model — the merge base. Advanced-only fields
	// (params, base_model_id, toolIds, data_warnings, ...) live here
	// untouched and are carried through on save.
	let mergeBase: any = { meta: {}, params: {} };

	let showAccessControlModal = false;

	const slugify = (s: string) =>
		s
			.toLowerCase()
			.trim()
			.replace(/[^a-z0-9]+/g, '-')
			.replace(/^-+|-+$/g, '');

	/**
	 * Slugify the name and avoid colliding with an existing model id.
	 * Appends a short random suffix only when a clean slug would clash —
	 * keeps the URL pretty for first-of-its-kind names. Belt-and-
	 * suspenders for the wizard auto-save path where the LLM may
	 * regenerate the same draft name across users.
	 */
	const uniqueSlug = (base: string): string => {
		const slug = slugify(base);
		if (!slug) return slug;
		if (!$models.find((m: any) => m?.id === slug)) return slug;
		return `${slug}-${crypto.randomUUID().slice(0, 6)}`;
	};

	/**
	 * The base model a fresh assistant starts on: the admin-configured
	 * default (first DEFAULT_MODELS id that resolves to a visible model),
	 * else the first non-preset, non-arena model. Returns '' if none.
	 */
	const computeDefaultBaseModelId = (): string => {
		const configuredDefault = ($config?.default_models || '')
			.split(',')
			.map((s: string) => s.trim())
			.find((mid: string) => mid && $models.some((m: any) => m?.id === mid));
		if (configuredDefault) return configuredDefault;
		const base = $models.find((m: any) => !m?.preset && !(m?.arena ?? false));
		return base?.id ?? '';
	};

	onMount(async () => {
		if (model) {
			mergeBase = JSON.parse(JSON.stringify(model));
			id = model.id;
			name = model.name ?? '';
			description = model?.meta?.description ?? '';
			system = model?.params?.system ?? '';
			profileImageUrl = model?.meta?.profile_image_url ?? profileImageUrl;
			knowledge = model?.meta?.knowledge ?? [];
			toggles = togglesFromMeta(model?.meta ?? {});
			accessGrants = model?.access_grants ?? [];
		} else if (draft) {
			// Fresh assistant pre-filled by the onboarding agent.
			name = draft.name ?? '';
			id = uniqueSlug(name);
			description = draft.description ?? '';
			system = draft.system_prompt ?? '';
			// Interview-time attachments (KBs picked + files uploaded
			// in the InterviewChat) ride along on draft.knowledge.
			knowledge = draft.knowledge ?? [];
			knowledgeHint = draft.knowledge_hint ?? '';
			toggles = {
				web_search: !!draft.capabilities?.web_search,
				image_generation: !!draft.capabilities?.image_generation,
				code_interpreter: !!draft.capabilities?.code_interpreter,
				document_writer: !!draft.capabilities?.document_writer,
				vision: !!draft.capabilities?.vision,
				file_upload: !!draft.capabilities?.file_upload,
				citations: !!draft.capabilities?.citations
			};
			mergeBase = {
				meta: {
					suggestion_prompts: (draft.conversation_starters ?? []).map((c: string) => ({
						content: c
					}))
				},
				params: {}
			};
		}
		// Seed the Model picker: the saved base model when editing, the
		// DEFAULT_MODELS default when creating (fresh or from a draft).
		baseModelId = model ? (model.base_model_id ?? '') : computeDefaultBaseModelId();
		await tick();
		savedSnapshot = _snapshot();
		hasBeenSaved = !!model;
		loaded = true;

		// Auto-save when entering with a fresh interview draft so the
		// assistant exists immediately. Manual saves only appear after
		// real edits — see ``isDirty`` and the Save-button conditional
		// below. The parent's onSubmit handles navigation to the edit
		// page on success.
		if (draft && !edit && !model) {
			autoSaving = true;
			submitHandler();
		}
	});

	/** Serialise the editable fields for dirty-state diffing. */
	const _snapshot = () =>
		JSON.stringify({
			id,
			name,
			description,
			system,
			baseModelId,
			profileImageUrl,
			knowledge,
			toggles,
			accessGrants
		});

	// Reactive dirty state — recomputes whenever any tracked field
	// changes. When savedSnapshot is null (mid-mount) we treat as not
	// dirty so the button doesn't flash in.
	$: liveSnapshot = JSON.stringify({
		id,
		name,
		description,
		system,
		baseModelId,
		profileImageUrl,
		knowledge,
		toggles,
		accessGrants
	});
	$: isDirty = savedSnapshot !== null && liveSnapshot !== savedSnapshot;

	const submitHandler = async (
		options: { skipNavigate?: boolean } = {}
	): Promise<boolean> => {
		if (name.trim() === '') {
			toast.error($i18n.t('Name is required.'));
			return false;
		}
		if (id.trim() === '') {
			id = uniqueSlug(name);
		}
		if (knowledge.some((item) => item.status === 'uploading')) {
			toast.error($i18n.t('Please wait until all files are uploaded.'));
			return false;
		}

		loading = true;

		// Deep-copy the merge base so advanced fields survive untouched.
		const info: any = JSON.parse(JSON.stringify(mergeBase));
		info.id = id;
		info.name = name;
		info.meta = info.meta ?? {};
		info.params = info.params ?? {};

		// The Model picker is the source of truth for the base model
		// (seeded at mount from DEFAULT_MODELS on create, or the saved base
		// on edit). Advanced can still override other base fields via the
		// carried-through mergeBase.
		info.base_model_id = baseModelId || null;

		info.meta.profile_image_url = profileImageUrl;
		info.meta.description = description.trim() === '' ? null : description;
		info.params.system = system.trim() === '' ? null : system;

		if (knowledge.length > 0) {
			info.meta.knowledge = knowledge;
		} else {
			delete info.meta.knowledge;
		}

		// Expand the six toggles into capabilities/defaultFeatureIds/builtinTools
		// without clobbering any other meta field.
		info.meta = applyToggles(info.meta, toggles);
		info.access_grants = accessGrants;

		await onSubmit(info, options);
		// Reset the dirty baseline to the values we just persisted, so
		// the Save button disappears until the user makes a new edit.
		// The parent's onSubmit typically navigates after creating, so
		// this often won't be observed for the auto-save path — but it
		// keeps the dirty model consistent for the manual-save path too.
		savedSnapshot = _snapshot();
		hasBeenSaved = true;
		autoSaving = false;
		loading = false;
		return true;
	};

	/**
	 * "New Knowledge" in the Knowledge dropdown: persist the assistant (so
	 * it has a stable id and keeps any unsaved edits), then navigate to the
	 * normal KB-create flow for the chosen ``type`` (local or a cloud-sync
	 * provider) with a returnTo back to this assistant's edit page. The KB
	 * detail page surfaces a "Back to assistant" affordance that returns
	 * here with ``?selectKb=<id>``, which the edit route attaches.
	 */
	const createKnowledgeFlow = async (type: string = 'local') => {
		if (name.trim() === '') {
			toast.error($i18n.t('Name your assistant first'));
			return;
		}
		const saved = await submitHandler({ skipNavigate: true });
		if (!saved) return;
		const returnTo = `/workspace/models/edit?id=${encodeURIComponent(id)}`;
		goto(
			`/workspace/knowledge/create?type=${encodeURIComponent(type)}&returnTo=${encodeURIComponent(returnTo)}`
		);
	};
</script>

{#if loaded}
	<AccessControlModal
		bind:show={showAccessControlModal}
		bind:accessGrants
		accessRoles={['read', 'write']}
		share={$user?.permissions?.sharing?.models || $user?.role === 'admin'}
		sharePublic={$user?.permissions?.sharing?.public_models || $user?.role === 'admin'}
		shareUsers={($user?.permissions?.access_grants?.allow_users ?? true) ||
			$user?.role === 'admin'}
	/>

	<div class="flex flex-col gap-5 max-w-3xl mx-auto w-full p-1">
		<div class="flex items-center justify-between">
			<div class="text-lg font-medium">
				{edit ? $i18n.t('Edit assistant') : $i18n.t('New assistant')}
			</div>
			<div class="flex gap-1.5">
				{#if edit || hasBeenSaved}
					<button
						class="bg-gray-50 shrink-0 hover:bg-gray-100 text-black dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white transition px-2 py-1 rounded-full flex gap-1 items-center"
						type="button"
						on:click={() => (showAccessControlModal = true)}
					>
						<LockClosed strokeWidth="2.5" className="size-3.5 shrink-0" />
						<div class="text-sm font-medium shrink-0">{$i18n.t('Sharing')}</div>
					</button>
				{/if}
				<button
					class="bg-gray-50 shrink-0 hover:bg-gray-100 text-black dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white transition px-2 py-1 rounded-full flex gap-1 items-center"
					type="button"
					on:click={onAdvanced}
				>
					<Cog6 strokeWidth="2.5" className="size-3.5 shrink-0" />
					<div class="text-sm font-medium shrink-0">{$i18n.t('Advanced')}</div>
				</button>
			</div>
		</div>

		<div>
			<div class="text-xs font-medium text-gray-500 mb-1">{$i18n.t('Name')}</div>
			<input
				class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden"
				bind:value={name}
				placeholder={$i18n.t('Name your assistant')}
			/>
		</div>

		<div>
			<div class="text-xs font-medium text-gray-500 mb-1">{$i18n.t('Description')}</div>
			<input
				class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden"
				bind:value={description}
				placeholder={$i18n.t('What does this assistant do?')}
			/>
		</div>

		<div>
			<div class="text-xs font-medium text-gray-500 mb-1">{$i18n.t('Instructions')}</div>
			<textarea
				class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden resize-y min-h-[7rem]"
				rows="6"
				bind:value={system}
				placeholder={$i18n.t('Tell the assistant how it should behave')}
			></textarea>
		</div>

		<div>
			<div class="text-xs font-medium text-gray-500 mb-1">{$i18n.t('Model')}</div>
			<Selector
				id="assistant-base-model"
				placeholder={$i18n.t('Select a model')}
				className="w-full"
				triggerClassName="text-sm"
				items={$models.map((m) => ({ value: m.id, label: m.name, model: m }))}
				bind:value={baseModelId}
			/>
		</div>

		<div>
			<div class="text-xs font-medium text-gray-500 mb-1">{$i18n.t('Knowledge')}</div>
			{#if knowledgeHint}
				<div class="text-xs text-gray-400 mb-2">💡 {knowledgeHint}</div>
			{/if}
			<Knowledge
				bind:selectedItems={knowledge}
				allowCreate
				on:create={(e) => createKnowledgeFlow(e.detail)}
			>
				<span slot="label"></span>
			</Knowledge>
		</div>

		<div>
			<div class="text-xs font-medium text-gray-500 mb-2">{$i18n.t('What it can do')}</div>
			<CapabilityToggles bind:toggles />
		</div>

		{#if isDirty || loading || autoSaving}
			<div class="flex justify-end">
				<button
					class="px-4 py-2 text-sm rounded-lg bg-black text-white dark:bg-white dark:text-black disabled:opacity-50"
					disabled={loading || autoSaving}
					on:click={() => submitHandler()}
				>
					{loading || autoSaving ? $i18n.t('Saving...') : $i18n.t('Save')}
				</button>
			</div>
		{/if}
	</div>
{/if}
