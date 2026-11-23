<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	// [Gradient] Assistants own a portable skill folder alongside their instructions.
	import { get } from 'svelte/store';
	import { assistantSkillImport } from '$lib/stores/assistantSkillImport';
	import { uploadFile, deleteFileById } from '$lib/apis/files';
	import { getKnowledgeById } from '$lib/apis/knowledge';
	import { getNoteById } from '$lib/apis/notes';
	import { getFolderById } from '$lib/apis/folders';
	import {
		getAssistantSkillFiles,
		putAssistantSkillFile,
		deleteAssistantSkillFile,
		exportAssistantSkill,
		type AssistantSkillFile
	} from '$lib/apis/models/skillFiles';
	import {
		parseAssistantSkill,
		restoreAssistantSettings,
		settingsForAssistantSkill,
		assistantSkillName,
		validateAssistantSkillFiles,
		droppedAssistantFiles
	} from '$lib/utils/assistantSkillBundle';
	import type { SkillBundleFile } from '$lib/utils/assistantBundleParser';
	import { formatFileSize } from '$lib/utils';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import { config, models, settings, user, pinnedModels } from '$lib/stores';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { updateUserSettings } from '$lib/apis/users';
	import {
		togglesFromMeta,
		applyToggles,
		simpleCapabilityOffered,
		type AssistantToggles
	} from '$lib/utils/assistantCapabilities';

	import Knowledge from './Knowledge.svelte';
	import CapabilityToggles from './Simple/CapabilityToggles.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import ArrowsPointingOut from '$lib/components/icons/ArrowsPointingOut.svelte';
	import { defaultLLMId, pickableLLMs } from '$lib/utils/assistants';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import Pin from '$lib/components/icons/Pin.svelte';
	import PinSlash from '$lib/components/icons/PinSlash.svelte';

	const i18n = getContext<import('svelte/store').Writable<import('i18next').i18n>>('i18n');

	// model = existing model when editing; null when creating fresh.
	export let model: any = null;
	export let edit = false;
	// options.skipNavigate lets callers persist without the parent route
	// navigating away — used by the "+ Add knowledge" flow, which saves
	// the assistant then navigates to the KB-create flow itself.
	export let onSubmit: (
		info: any,
		options?: { skipNavigate?: boolean }
	) => Promise<boolean | void> | boolean | void;

	let loaded = false;
	let loading = false;
	// Snapshot of the field values at "saved" baseline. After onMount
	// it's the just-loaded values; after a successful save
	// it's the values that were just persisted. isDirty diffs the live
	// snapshot against this baseline to drive the Save button.
	let savedSnapshot: string | null = null;
	// Whether the assistant has been saved at least once in this
	// session — drives Share button visibility (we don't want Share to
	// appear before there's anything to share).
	let hasBeenSaved = false;

	// Edited-in-the-simple-view fields:
	let id = '';
	let name = '';
	let description = '';
	let system = '';
	let defaultModelId = '';
	let profileImageUrl = `${WEBUI_BASE_URL}/static/favicon.png`;
	let knowledge: any[] = [];
	let toggles: AssistantToggles = togglesFromMeta({});
	let accessGrants: any[] = [];
	let skillFiles: AssistantSkillFile[] = [];
	let skillBusy = false;
	let addFilesInput: HTMLInputElement;
	let importSkillInput: HTMLInputElement;
	let pendingSkillImport: File | null = null;
	let showSkillConfirmation = false;
	$: canWrite = !edit || model?.write_access === true;
	$: workspaceOffered = simpleCapabilityOffered('workspace', $config?.features);

	// [Gradient] Sidebar pin, right here in the assistant editor: the
	// model selector (upstream's pin surface) is hidden behind the agent
	// picker, so this is the one place a user can reach the toggle.
	// Effective pins include admin defaults, with the same handler shape as the
	// workspace list's pinModelHandler.
	$: isPinned = $pinnedModels.includes(id);

	const pinModelHandler = async () => {
		let nextPinnedModels: string[] = [...$pinnedModels];

		if (nextPinnedModels.includes(id)) {
			nextPinnedModels = nextPinnedModels.filter((modelId) => modelId !== id);
		} else {
			nextPinnedModels = [...new Set([...nextPinnedModels, id])];
		}

		settings.set({ ...$settings, pinnedModels: nextPinnedModels });
		await updateUserSettings(localStorage.token, { ui: $settings });
	};
	// The full original model — the merge base. Advanced-only fields
	// (params, base_model_id, toolIds, data_warnings, ...) live here
	// untouched and are carried through on save.
	let mergeBase: any = { meta: {}, params: {} };

	let showAccessControlModal = false;
	let showInstructionsModal = false;

	// [Gradient] Every LLM the assistant's tools allow is listed; Workspace greys out the ones without it.
	const supportsWorkspace = (model: any) => model?.info?.meta?.capabilities?.workspace === true;
	$: liveMeta = applyToggles({ ...mergeBase.meta, knowledge }, toggles);
	$: defaultModelOptions = pickableLLMs($models, {
		id,
		info: {
			base_model_id: mergeBase.base_model_id ?? '',
			meta: { ...liveMeta, capabilities: { ...liveMeta.capabilities, workspace: false } }
		}
	});
	// A stored default the tools or Workspace rule out falls back to no preference.
	$: if (
		defaultModelId &&
		!defaultModelOptions.some(
			(model) => model.id === defaultModelId && (!toggles.workspace || supportsWorkspace(model))
		)
	) {
		defaultModelId = '';
	}
	$: defaultModel = defaultModelOptions.find((model) => model.id === defaultModelId);
	$: someModelsLackWorkspace = defaultModelOptions.some((model) => !supportsWorkspace(model));
	// A chosen default model without Workspace keeps the switch off until another model is chosen.
	$: workspaceBlocked = !toggles.workspace && !!defaultModel && !supportsWorkspace(defaultModel);

	const slugify = (s: string) =>
		s
			.toLowerCase()
			.trim()
			.replace(/[^a-z0-9]+/g, '-')
			.replace(/^-+|-+$/g, '');

	/**
	 * Slugify the name and avoid colliding with an existing model id.
	 * Appends a short random suffix only when a clean slug would clash —
	 * keeps the URL pretty for first-of-its-kind names.
	 */
	const uniqueSlug = (base: string): string => {
		const slug = slugify(base);
		if (!slug) return slug;
		if (!$models.find((m: any) => m?.id === slug)) return slug;
		return `${slug}-${crypto.randomUUID().slice(0, 6)}`;
	};

	// The legacy marker distinguishes an assistant from an admin override row.
	const computeDefaultBaseModelId = (): string =>
		defaultLLMId(
			$models,
			($config?.default_models || '').split(',').map((id) => id.trim())
		);

	onMount(async () => {
		if (model) {
			mergeBase = JSON.parse(JSON.stringify(model));
			id = model.id;
			name = model.name ?? '';
			description = model?.meta?.description ?? '';
			system = model?.params?.system ?? '';
			defaultModelId = model?.meta?.defaultModelId ?? '';
			profileImageUrl = model?.meta?.profile_image_url ?? profileImageUrl;
			knowledge = model?.meta?.knowledge ?? [];
			toggles = togglesFromMeta(model?.meta ?? {});
			accessGrants = model?.access_grants ?? [];
		}
		await tick();
		savedSnapshot = _snapshot();
		hasBeenSaved = edit;
		loaded = true;
		if (hasBeenSaved && canWrite) await refreshSkillFiles();
		const imported = get(assistantSkillImport);
		if (imported) {
			assistantSkillImport.set(null);
			await importSkill(imported);
		}
	});

	/** Serialise the editable fields for dirty-state diffing. */
	const _snapshot = () =>
		JSON.stringify({
			id,
			name,
			description,
			system,
			defaultModelId,
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
		defaultModelId,
		profileImageUrl,
		knowledge,
		toggles,
		accessGrants
	});
	$: isDirty = savedSnapshot !== null && liveSnapshot !== savedSnapshot;

	const submitHandler = async (options: { skipNavigate?: boolean } = {}): Promise<boolean> => {
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
		delete info.meta.skillIds;
		info.params = info.params ?? {};

		// The chat's model picker chooses the LLM; base_model_id only marks
		// the row as an assistant (see isAssistant).
		info.base_model_id ||= computeDefaultBaseModelId();
		if (!info.base_model_id) {
			toast.error($i18n.t('Base Model is required.'));
			loading = false;
			return false;
		}

		info.meta.profile_image_url = profileImageUrl;
		info.meta.description = description.trim() === '' ? null : description;
		info.params.system = system.trim() === '' ? null : system;
		if (defaultModelId) {
			info.meta.defaultModelId = defaultModelId;
		} else {
			delete info.meta.defaultModelId;
		}

		if (knowledge.length > 0) {
			info.meta.knowledge = knowledge;
		} else {
			delete info.meta.knowledge;
		}

		// Expand the six toggles into capabilities/defaultFeatureIds/builtinTools
		// without clobbering any other meta field.
		info.meta = applyToggles(info.meta, toggles);
		info.access_grants = accessGrants;

		if ((await onSubmit(info, options)) === false) {
			loading = false;
			return false;
		}
		// Reset the dirty baseline to the values we just persisted, so
		// the Save button disappears until the user makes a new edit.
		savedSnapshot = _snapshot();
		hasBeenSaved = true;
		mergeBase = info;
		loading = false;
		return true;
	};

	const skillError = (error: unknown) => {
		const key = error instanceof Error ? error.message : String(error);
		const messages: Record<string, string> = {
			INVALID_PATH:
				'Invalid skill file path. Use relative paths; SKILL.md and soev.json are reserved at the root.',
			TOO_MANY_FILES: 'A skill folder can contain at most 498 files.',
			BUNDLE_TOO_LARGE: 'The skill folder must not exceed 25 MiB.',
			NO_SKILL_MD: 'Not a valid skill bundle (no SKILL.md).'
		};
		toast.error($i18n.t(messages[key] || 'Could not import the skill or its files.'));
	};

	const refreshSkillFiles = async () => {
		if (!canWrite) return;
		try {
			skillFiles = await getAssistantSkillFiles(localStorage.token, id);
		} catch {
			toast.error($i18n.t('Could not load skill files.'));
		}
	};

	const uploadSkillFiles = async (files: SkillBundleFile[]) => {
		const replaced = new Set(files.map((file) => file.path));
		validateAssistantSkillFiles([
			...skillFiles
				.filter((file) => !replaced.has(file.path))
				.map((file) => ({
					path: file.path,
					blob: { size: file.size } as Blob
				})),
			...files
		]);
		if (!hasBeenSaved && !(await submitHandler({ skipNavigate: true }))) return;
		for (const file of files) {
			const uploaded = await uploadFile(
				localStorage.token,
				new File([file.blob], file.path.split('/').pop()!, { type: file.blob.type }),
				null,
				false
			);
			if (!uploaded) throw new Error('UPLOAD_FAILED');
			try {
				await putAssistantSkillFile(localStorage.token, id, file.path, uploaded.id);
			} catch (error) {
				await deleteFileById(localStorage.token, uploaded.id);
				// The server supplies a fixed, translated ownership or admission refusal.
				toast.error(error instanceof Error ? error.message : String(error));
				return;
			}
		}
	};

	const addSkillFiles = async (files: SkillBundleFile[]) => {
		if (skillBusy) return;
		skillBusy = true;
		try {
			await uploadSkillFiles(files);
		} catch (error) {
			skillError(error);
		} finally {
			if (hasBeenSaved && canWrite) await refreshSkillFiles();
			skillBusy = false;
		}
	};

	const importSkill = async (file: File) => {
		if (skillBusy) return;
		skillBusy = true;
		try {
			const imported = await parseAssistantSkill(file);
			if (!hasBeenSaved) {
				name = imported.name;
				description = imported.description;
				const readable = new Map<string, any>();
				const restored = await restoreAssistantSettings(
					settingsForAssistantSkill(imported.settings, imported.files.length > 0, workspaceOffered),
					$config?.features,
					$models,
					async (type, id) => {
						try {
							const item = await (
								type === 'collection'
									? getKnowledgeById
									: type === 'note'
										? getNoteById
										: getFolderById
							)(localStorage.token, id);
							if (!item) return false;
							readable.set(id, item);
							return true;
						} catch {
							return false;
						}
					}
				);
				mergeBase = { ...mergeBase, meta: restored.meta };
				toggles = togglesFromMeta(restored.meta);
				defaultModelId = restored.meta.defaultModelId || '';
				knowledge = (restored.meta.knowledge || []).map((entry: any) => ({
					...entry,
					name: readable.get(entry.id)?.name || readable.get(entry.id)?.title || entry.id
				}));
				if (restored.omitted.length)
					toast.warning(
						$i18n.t('Import left out unavailable settings or knowledge: {{items}}', {
							items: restored.omitted.join(', ')
						})
					);
			}
			system = imported.instructions;
			if (imported.missing.length)
				toast.warning(
					$i18n.t('SKILL.md references files not in the bundle: {{paths}}', {
						paths: imported.missing.join(', ')
					})
				);
			if (hasBeenSaved && !(await submitHandler({ skipNavigate: true }))) return;
			if (imported.files.length) {
				if (workspaceOffered) await uploadSkillFiles(imported.files);
				else
					toast.warning(
						$i18n.t(
							'Instructions imported. Skill files were skipped because Workspace is unavailable here.'
						)
					);
			}
		} catch (error) {
			skillError(error);
		} finally {
			if (hasBeenSaved && canWrite) await refreshSkillFiles();
			skillBusy = false;
		}
	};

	const requestSkillImport = (file?: File) => {
		if (!file || skillBusy) return;
		if (hasBeenSaved) {
			pendingSkillImport = file;
			showSkillConfirmation = true;
		} else void importSkill(file);
	};

	const dropSkillFiles = async (event: DragEvent) => {
		if (!event.dataTransfer || skillBusy) return;
		try {
			const files = await droppedAssistantFiles(event.dataTransfer);
			if (files.length === 1 && /\.(skill|zip|md)$/i.test(files[0].path))
				requestSkillImport(new File([files[0].blob], files[0].path));
			else await addSkillFiles(files);
		} catch (error) {
			skillError(error);
		}
	};

	const removeSkillFile = async (path: string) => {
		skillBusy = true;
		try {
			await deleteAssistantSkillFile(localStorage.token, id, path);
			await refreshSkillFiles();
		} catch {
			toast.error($i18n.t('Could not remove the skill file.'));
		} finally {
			skillBusy = false;
		}
	};

	const exportSkill = async () => {
		skillBusy = true;
		try {
			if (isDirty && !(await submitHandler({ skipNavigate: true }))) return;
			const url = URL.createObjectURL(await exportAssistantSkill(localStorage.token, id));
			const link = document.createElement('a');
			link.href = url;
			link.download = `${assistantSkillName(name)}.skill`;
			link.click();
			setTimeout(() => URL.revokeObjectURL(url), 1000);
		} catch {
			toast.error($i18n.t('Could not export the assistant as a skill.'));
		} finally {
			skillBusy = false;
		}
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
	<!-- [Gradient] One importer serves new assistants, saved assistants and folder drops. -->
	<input
		type="file"
		class="hidden"
		accept=".skill,.zip,.md"
		bind:this={importSkillInput}
		on:change={() => {
			requestSkillImport(importSkillInput.files?.[0]);
			importSkillInput.value = '';
		}}
	/>
	<input
		type="file"
		class="hidden"
		multiple
		bind:this={addFilesInput}
		on:change={() => {
			void addSkillFiles(
				[...(addFilesInput.files || [])].map((file) => ({
					path: file.webkitRelativePath || file.name,
					blob: file
				}))
			);
			addFilesInput.value = '';
		}}
	/>
	<ConfirmDialog
		bind:show={showSkillConfirmation}
		title={$i18n.t('Import skill')}
		message={$i18n.t(
			'Replace the instructions and add the imported files? Files at existing paths will be replaced. The other assistant settings are kept.'
		)}
		on:confirm={() => {
			if (pendingSkillImport) void importSkill(pendingSkillImport);
			pendingSkillImport = null;
		}}
	/>
	<AccessControlModal
		bind:show={showAccessControlModal}
		bind:accessGrants
		accessRoles={['read', 'write']}
		share={$user?.permissions?.sharing?.models || $user?.role === 'admin'}
		sharePublic={$user?.permissions?.sharing?.public_models || $user?.role === 'admin'}
		shareUsers={($user?.permissions?.access_grants?.allow_users ?? true) || $user?.role === 'admin'}
	/>

	<Modal size="3xl" bind:show={showInstructionsModal}>
		<div class="flex flex-col gap-3 p-5">
			<div class="flex items-center justify-between">
				<label for="assistant-instructions-large" class="text-sm font-medium"
					>{$i18n.t('Instructions')}</label
				>
				<button
					type="button"
					class="px-3 py-1.5 text-sm rounded-lg bg-black text-white dark:bg-white dark:text-black"
					on:click={() => (showInstructionsModal = false)}>{$i18n.t('Close')}</button
				>
			</div>
			<textarea
				id="assistant-instructions-large"
				class="w-full h-[75vh] rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden resize-none"
				bind:value={system}
				placeholder={$i18n.t('Tell the assistant how it should behave')}
			></textarea>
		</div>
	</Modal>

	<div class="flex flex-col gap-5 max-w-3xl mx-auto w-full p-1">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<div class="text-lg font-medium whitespace-nowrap">
				{edit ? $i18n.t('Edit assistant') : $i18n.t('New assistant')}
			</div>
			<div class="flex flex-wrap gap-1.5">
				{#if !hasBeenSaved}
					<button
						type="button"
						class="bg-gray-50 dark:bg-gray-850 px-2 py-1 rounded-full text-sm"
						disabled={skillBusy}
						on:click={() => importSkillInput.click()}>{$i18n.t('Import skill')}</button
					>
				{/if}
				{#if (edit || hasBeenSaved) && id}
					<button
						class="bg-gray-50 shrink-0 hover:bg-gray-100 text-black dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white transition px-2 py-1 rounded-full flex gap-1 items-center"
						type="button"
						aria-pressed={isPinned}
						on:click={pinModelHandler}
					>
						{#if isPinned}
							<PinSlash strokeWidth="2.5" className="size-3.5 shrink-0" />
							<div class="text-sm font-medium shrink-0">{$i18n.t('Hide from Sidebar')}</div>
						{:else}
							<Pin strokeWidth="2.5" className="size-3.5 shrink-0" />
							<div class="text-sm font-medium shrink-0">{$i18n.t('Keep in Sidebar')}</div>
						{/if}
					</button>
				{/if}
				{#if canWrite && (edit || hasBeenSaved)}
					<button
						type="button"
						class="bg-gray-50 dark:bg-gray-850 px-2 py-1 rounded-full text-sm"
						disabled={skillBusy || loading}
						on:click={exportSkill}>{$i18n.t('Export as skill')}</button
					>
				{/if}
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
			<div class="flex items-center justify-between mb-1">
				<label for="assistant-instructions" class="text-xs font-medium text-gray-500"
					>{$i18n.t('Instructions')}</label
				>
				<!-- [Gradient] Long instructions (an imported SKILL.md) get a large editor of their own. -->
				<button
					type="button"
					class="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-900 dark:hover:text-gray-100"
					on:click={() => (showInstructionsModal = true)}
				>
					<ArrowsPointingOut className="size-3.5" />
					{$i18n.t('Open large editor')}
				</button>
			</div>
			<textarea
				id="assistant-instructions"
				class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden resize-y min-h-[7rem]"
				rows="10"
				bind:value={system}
				placeholder={$i18n.t('Tell the assistant how it should behave')}
			></textarea>
		</div>

		<!-- [Gradient] An optional starting LLM; the chat keeps its own later choices. -->
		<div>
			<label for="assistant-default-model" class="block text-xs font-medium text-gray-500 mb-1"
				>{$i18n.t('Default model')}</label
			>
			<select
				id="assistant-default-model"
				class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden"
				bind:value={defaultModelId}
			>
				<option value="">{$i18n.t('No preference')}</option>
				{#each defaultModelOptions as model}
					{#if toggles.workspace && !supportsWorkspace(model)}
						<option value={model.id} disabled
							>{$i18n.t('{{model}} (no Workspace)', { model: model.name ?? model.id })}</option
						>
					{:else}
						<option value={model.id}>{model.name ?? model.id}</option>
					{/if}
				{/each}
			</select>
			<div class="text-xs text-gray-500 mt-1">
				{$i18n.t(
					'Selected when someone starts a new chat with this assistant. They can still switch model in the chat.'
				)}
				{#if toggles.workspace && someModelsLackWorkspace}
					{$i18n.t('Greyed-out models do not support Workspace, which is on for this assistant.')}
				{/if}
			</div>
		</div>

		<div>
			<div class="text-xs font-medium text-gray-500 mb-1">{$i18n.t('Knowledge')}</div>
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
			<CapabilityToggles bind:toggles exclude={['workspace']} />
		</div>

		<!-- [Gradient] Workspace has its own section: it limits the models and owns the skill files. -->
		{#if workspaceOffered}
			<div class="border-t border-gray-100 dark:border-gray-850 pt-4">
				<div class="flex items-center justify-between gap-3">
					<div id="assistant-workspace-label" class="text-sm font-medium">
						{$i18n.t('Workspace')}
					</div>
					<Switch
						bind:state={toggles.workspace}
						ariaLabelledbyId="assistant-workspace-label"
						disabled={workspaceBlocked}
					/>
				</div>
				<p class="text-xs text-gray-500 mt-1">
					{$i18n.t(
						'The assistant gets its own sandbox to read, write and run files. Not every model supports this: with Workspace on, the assistant only runs on models that do.'
					)}
				</p>
				{#if workspaceBlocked}
					<p class="text-xs text-amber-700 dark:text-amber-400 mt-1">
						{$i18n.t(
							'The default model {{model}} does not support Workspace. Choose another default model to turn it on.',
							{ model: defaultModel?.name ?? defaultModelId }
						)}
					</p>
				{/if}
			</div>
		{/if}

		{#if canWrite && workspaceOffered}
			{#if toggles.workspace}
				<section class="border border-gray-200 dark:border-gray-800 rounded-lg p-3 space-y-2">
					<div class="text-xs font-medium text-gray-500">{$i18n.t('Skill files')}</div>
					<p class="text-xs text-gray-500">
						{$i18n.t('Copied into the sandbox at the start of every chat with this assistant.')}
					</p>
					{#each skillFiles as file (file.path)}
						<div
							class="flex items-center gap-2 bg-gray-50 dark:bg-gray-850 rounded-lg px-3 py-2 text-xs"
						>
							<span class="font-mono break-all flex-1">{file.path}</span>
							<span class="text-gray-500 whitespace-nowrap">{formatFileSize(file.size)}</span>
							<button
								type="button"
								disabled={skillBusy}
								aria-label={$i18n.t('Remove {{path}}', { path: file.path })}
								on:click={() => removeSkillFile(file.path)}>{$i18n.t('Remove')}</button
							>
						</div>
					{/each}
					<div class="flex gap-2 text-xs">
						<button
							type="button"
							class="bg-gray-50 dark:bg-gray-850 rounded-lg px-3 py-2"
							disabled={skillBusy || loading}
							on:click={() => addFilesInput.click()}>{$i18n.t('Add files')}</button
						>
						<button
							type="button"
							class="bg-gray-50 dark:bg-gray-850 rounded-lg px-3 py-2"
							disabled={skillBusy || loading}
							on:click={() => importSkillInput.click()}>{$i18n.t('Import skill')}</button
						>
					</div>
					<div
						role="region"
						aria-label={$i18n.t('Skill files')}
						class="border border-dashed border-gray-300 dark:border-gray-700 rounded-lg p-4 text-xs text-gray-500 text-center"
						on:dragover|preventDefault
						on:drop|preventDefault={dropSkillFiles}
					>
						{$i18n.t('Drop files, a folder or a .skill / .zip bundle here')}
					</div>
				</section>
			{:else}
				<p class="text-xs text-gray-500">
					{$i18n.t(
						'Turn on Workspace to give this assistant its own files and scripts, or to import a skill.'
					)}
				</p>
			{/if}
		{/if}

		{#if isDirty || loading}
			<div class="flex justify-end">
				<button
					class="px-4 py-2 text-sm rounded-lg bg-black text-white dark:bg-white dark:text-black disabled:opacity-50"
					disabled={loading || skillBusy}
					on:click={() => submitHandler()}
				>
					{loading ? $i18n.t('Saving...') : $i18n.t('Save')}
				</button>
			</div>
		{/if}
	</div>
{/if}
