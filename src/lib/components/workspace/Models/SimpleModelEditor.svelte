<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
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
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import Cog6 from '$lib/components/icons/Cog6.svelte';

	const i18n = getContext('i18n');

	// model = existing model when editing; null when creating fresh.
	export let model: any = null;
	// draft = AssistantDraft from the onboarding agent; null when not from the wizard.
	export let draft: any = null;
	export let edit = false;
	export let onSubmit: (info: any) => Promise<void> | void;
	export let onAdvanced: () => void;

	let loaded = false;
	let loading = false;

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
			id = slugify(name);
			description = draft.description ?? '';
			system = draft.system_prompt ?? '';
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
		await tick();
		loaded = true;
	});

	const submitHandler = async () => {
		if (name.trim() === '') {
			toast.error($i18n.t('Name is required.'));
			return;
		}
		if (id.trim() === '') {
			id = slugify(name);
		}
		if (knowledge.some((item) => item.status === 'uploading')) {
			toast.error($i18n.t('Please wait until all files are uploaded.'));
			return;
		}

		loading = true;

		// Deep-copy the merge base so advanced fields survive untouched.
		const info: any = JSON.parse(JSON.stringify(mergeBase));
		info.id = id;
		info.name = name;
		info.meta = info.meta ?? {};
		info.params = info.params ?? {};

		// On create, prefer the admin-configured DEFAULT_MODELS (first id
		// that actually resolves to a model the user can see). Falls back
		// to the first non-preset, non-arena model. Advanced lets power
		// users override the base model.
		if (!edit && !info.base_model_id) {
			const configuredDefault = ($config?.default_models || '')
				.split(',')
				.map((s: string) => s.trim())
				.find((id: string) => id && $models.some((m: any) => m?.id === id));
			if (configuredDefault) {
				info.base_model_id = configuredDefault;
			} else {
				const base = $models.find((m: any) => !m?.preset && !(m?.arena ?? false));
				info.base_model_id = base?.id ?? null;
			}
		}

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

		await onSubmit(info);
		loading = false;
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
				<button
					class="bg-gray-50 shrink-0 hover:bg-gray-100 text-black dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white transition px-2 py-1 rounded-full flex gap-1 items-center"
					type="button"
					on:click={() => (showAccessControlModal = true)}
				>
					<LockClosed strokeWidth="2.5" className="size-3.5 shrink-0" />
					<div class="text-sm font-medium shrink-0">{$i18n.t('Sharing')}</div>
				</button>
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
			<div class="text-xs font-medium text-gray-500 mb-1">{$i18n.t('Knowledge')}</div>
			{#if knowledgeHint}
				<div class="text-xs text-gray-400 mb-2">💡 {knowledgeHint}</div>
			{/if}
			<Knowledge bind:selectedItems={knowledge} />
		</div>

		<div>
			<div class="text-xs font-medium text-gray-500 mb-2">{$i18n.t('What it can do')}</div>
			<CapabilityToggles bind:toggles />
		</div>

		<div class="flex justify-end">
			<button
				class="px-4 py-2 text-sm rounded-lg bg-black text-white dark:bg-white dark:text-black disabled:opacity-50"
				disabled={loading}
				on:click={submitHandler}
			>
				{loading ? $i18n.t('Saving...') : $i18n.t('Save')}
			</button>
		</div>
	</div>
{/if}
