<script lang="ts">
	import { toast } from 'svelte-sonner';
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { onMount, getContext, tick, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	const i18n = getContext<Writable<I18n>>('i18n');

	import { WEBUI_NAME, user, skills as _skills } from '$lib/stores';
	import { goto } from '$app/navigation';
	import {
		getSkills,
		getSkillById,
		getSkillItems,
		exportSkills,
		createNewSkill,
		getSkillFileList,
		getSkillFileContentBlob,
		deleteSkillById,
		toggleSkillById
	} from '$lib/apis/skills';
	import { formatSkillName, slugify } from '$lib/utils';
	import {
		parseSkillBundle,
		buildSkillBundle,
		encodeSkillBundle,
		parseSkillMetadata,
		findMissingSkillFiles
	} from '$lib/utils/skills/bundle';

	import Tooltip from '../common/Tooltip.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import EllipsisHorizontal from '../icons/EllipsisHorizontal.svelte';
	import GarbageBin from '../icons/GarbageBin.svelte';
	import Search from '../icons/Search.svelte';
	import XMark from '../icons/XMark.svelte';
	import Spinner from '../common/Spinner.svelte';
	import ViewSelector from './common/ViewSelector.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import Switch from '../common/Switch.svelte';
	import SkillMenu from './Skills/SkillMenu.svelte';
	import Pagination from '../common/Pagination.svelte';

	let shiftKey = false;
	let loaded = false;

	let importFiles: FileList | null;
	let importing = false;
	let importInputElement: HTMLInputElement;

	let query = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	let selectedSkill = null;
	let showDeleteConfirm = false;

	let filteredItems = null;
	let total = null;
	let loading = false;

	let tagsContainerElement: HTMLDivElement;
	let viewOption = '';
	let sortKey = 'updated_at';
	let sortDirection = 'desc';
	let openSkillMenuId: string | null = null;
	let page = 1;

	const exportAllHandler = async () => {
		const exported = await exportSkills(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (exported) {
			saveAs(
				new Blob([JSON.stringify(exported)], { type: 'application/json' }),
				`skills-export-${Date.now()}.json`
			);
		}
	};

	const loadSkillItems = async () => {
		if (!loaded) return;

		loading = true;
		try {
			const res = await getSkillItems(
				localStorage.token,
				query,
				viewOption,
				page,
				sortKey,
				sortDirection
			).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (res) {
				filteredItems = res.items;
				total = res.total;
			}
		} catch (err) {
			console.error(err);
		} finally {
			loading = false;
		}
	};

	const handleSearchInput = () => {
		loading = true;
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			if (page !== 1) {
				page = 1;
			} else {
				loadSkillItems();
			}
		}, 300);
	};

	// Immediate response to page/filter changes
	$: if (
		loaded &&
		page &&
		viewOption !== undefined &&
		sortKey !== undefined &&
		sortDirection !== undefined
	) {
		loadSkillItems();
	}

	const openSkill = (skill) => {
		goto(`/workspace/skills/edit?id=${encodeURIComponent(skill.id)}`);
	};

	const shouldIgnoreRowClick = (target: EventTarget | null) => {
		return (
			target instanceof Element &&
			!!target.closest('button, a, input, [role="menu"], [data-skill-toggle]')
		);
	};

	const cloneHandler = async (skill) => {
		const _skill = await getSkillById(localStorage.token, skill.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (_skill) {
			sessionStorage.skill = JSON.stringify({
				..._skill,
				id: `${_skill.id}_clone`,
				name: `${_skill.name} (Clone)`
			});
			goto('/workspace/skills/create');
		}
	};

	const exportHandler = async (skill) => {
		const _skill = await getSkillById(localStorage.token, skill.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (_skill) {
			let blob = new Blob([JSON.stringify([_skill])], {
				type: 'application/json'
			});
			saveAs(blob, `skill-${_skill.id}-export-${Date.now()}.json`);
		}
	};

	const importSkillBundle = async (file: File) => {
		let parsed;
		try {
			parsed = await parseSkillBundle(file);
		} catch {
			toast.error($i18n.t('Not a valid skill bundle (no SKILL.md).'));
			return;
		}

		// Non-blocking guardrail: if the SKILL.md references bundle files (e.g.
		// assets/template.docx) that aren't in the bundle, the skill will fail at
		// run time with "cannot stat ...". Warn but still import.
		const missingFiles = findMissingSkillFiles(parsed.skillMd, parsed.files);
		if (missingFiles.length) {
			toast.warning(
				$i18n.t('SKILL.md references files not in the bundle: {{paths}}', {
					paths: missingFiles.join(', ')
				})
			);
		}

		let fm: { name?: string; description?: string };
		try {
			fm = parseSkillMetadata(parsed.skillMd);
		} catch {
			toast.error($i18n.t('Not a valid skill bundle (no SKILL.md).'));
			return;
		}
		const baseName = file.name.replace(/\.(skill|zip)$/i, '');
		const name = formatSkillName(fm.name || baseName);

		let created;
		try {
			created = await createNewSkill(localStorage.token, {
				id: slugify(name),
				name,
				description: fm.description || '',
				content: parsed.skillMd,
				is_active: true,
				bundle: await encodeSkillBundle(parsed.skillMd, parsed.files),
				meta: { tags: [] },
				access_grants: []
			});
		} catch (error) {
			toast.error(`${error}`);
			return;
		}

		toast.success($i18n.t('Skill “{{name}}” uploaded', { name: created.name }));
		page = 1;
		loadSkillItems();
		_skills.set(await getSkills(localStorage.token));
	};

	const exportBundleHandler = async (skill) => {
		const _skill = await getSkillById(localStorage.token, skill.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (!_skill) return;

		// The file-list route returns at most the first page (no pagination param),
		// so a skill with more files than that can only be partially exported. Warn
		// loudly rather than silently dropping the remainder.
		const res = await getSkillFileList(localStorage.token, _skill.id).catch(() => null);
		const items = res?.items ?? [];
		const total = res?.total ?? items.length;
		if (total > items.length) {
			toast.warning(
				$i18n.t('Only the first {{count}} of {{total}} files were exported.', {
					count: items.length,
					total
				})
			);
		}

		const bundleFiles = [];
		for (const item of items) {
			try {
				const blob = await getSkillFileContentBlob(localStorage.token, _skill.id, item.path);
				bundleFiles.push({ path: item.path, blob });
			} catch {
				toast.error($i18n.t('Failed to export file: {{path}}', { path: item.path }));
			}
		}

		// Wrap the bundle in a <slug>/ directory (the layout Claude produces and
		// expects), and name the file by slug so it has no spaces/capitals.
		const blob = await buildSkillBundle(_skill.content || '', bundleFiles, _skill.id);
		saveAs(blob, `${_skill.id}.skill`);
	};

	const deleteHandler = async (skill) => {
		const res = await deleteSkillById(localStorage.token, skill.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Skill deleted successfully'));
		}

		page = 1;
		loadSkillItems();
		await _skills.set(await getSkills(localStorage.token));
	};

	onMount(() => {
		viewOption = localStorage?.workspaceViewOption || '';
		loaded = true;

		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key === 'Shift') {
				shiftKey = true;
			}
		};

		const onKeyUp = (event: KeyboardEvent) => {
			if (event.key === 'Shift') {
				shiftKey = false;
			}
		};

		const onBlur = () => {
			shiftKey = false;
		};

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);
		window.addEventListener('blur', onBlur);

		return () => {
			clearTimeout(searchDebounceTimer);
			window.removeEventListener('keydown', onKeyDown);
			window.removeEventListener('keyup', onKeyUp);
			window.removeEventListener('blur', onBlur);
		};
	});

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});
</script>

<svelte:head>
	<!-- LICENSE covers this Open WebUI browser-title identifier.
	Do not alter, remove, obscure, or replace it except as LICENSE permits:
	https://docs.openwebui.com/license. -->
	<title>
		{$i18n.t('Skills')} / {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<!-- [Gradient] Upload creates the complete bundle directly; the editor is a detail view. -->
	<input
		bind:this={importInputElement}
		bind:files={importFiles}
		type="file"
		accept=".zip,.skill"
		hidden
		on:change={async () => {
			const file = importFiles?.[0];
			if (!file || importing) return;
			importing = true;
			try {
				await importSkillBundle(file);
			} finally {
				importing = false;
				importInputElement.value = '';
			}
		}}
	/>

	<div class="mb-5 flex flex-wrap items-start justify-between gap-3">
		<div>
			<h1 class="text-xl font-medium">{$i18n.t('Skills')}</h1>
			<p class="mt-1 text-sm text-gray-500">
				{$i18n.t('Active skills are used automatically when the workspace is on.')}
			</p>
		</div>
		<div class="flex items-center gap-3">
			{#if $user?.role === 'admin' || $user?.permissions?.workspace?.skills_export}
				<button
					type="button"
					class="text-sm text-gray-500 hover:underline"
					on:click={exportAllHandler}
				>
					{$i18n.t('Export JSON')}
				</button>
			{/if}
			{#if $user?.role === 'admin' || $user?.permissions?.workspace?.skills}
				<a class="text-sm text-gray-500 hover:underline" href="/workspace/skills/create">
					{$i18n.t('Create from scratch')}
				</a>
			{/if}
			{#if $user?.role === 'admin' || $user?.permissions?.workspace?.skills_import}
				<button
					type="button"
					class="focus-ring rounded-xl bg-gray-900 px-3 py-2 text-sm text-white dark:bg-white dark:text-gray-900 disabled:opacity-50"
					disabled={importing}
					on:click={() => importInputElement.click()}
				>
					{$i18n.t('Upload skill')}
				</button>
			{/if}
		</div>
	</div>

	<div class="space-y-1">
		<div class="flex h-8 w-full items-center gap-2">
			<div class="flex min-w-0 flex-1">
				<div class=" self-center ml-1 mr-3">
					<Search className="size-3.5" />
				</div>
				<input
					class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
					bind:value={query}
					on:input={handleSearchInput}
					aria-label={$i18n.t('Search Skills')}
					placeholder={$i18n.t('Search Skills')}
				/>
				{#if query}
					<div class="self-center pl-1.5 translate-y-[0.5px] rounded-l-xl bg-transparent">
						<button
							class="p-0.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-900 transition"
							aria-label={$i18n.t('Clear search')}
							on:click={() => {
								query = '';
								handleSearchInput();
							}}
						>
							<XMark className="size-3" strokeWidth="2" />
						</button>
					</div>
				{/if}
			</div>

			<div
				class="flex max-w-[55%] shrink-0 overflow-x-auto scrollbar-none"
				bind:this={tagsContainerElement}
				on:wheel={(e) => {
					if (e.deltaY !== 0) {
						e.preventDefault();
						e.currentTarget.scrollLeft += e.deltaY;
					}
				}}
			>
				<div
					class="flex w-fit gap-0.5 text-center text-sm rounded-full bg-transparent whitespace-nowrap"
				>
					<ViewSelector
						bind:value={viewOption}
						align="end"
						onChange={async (value) => {
							localStorage.workspaceViewOption = value;
							page = 1;
							await tick();
						}}
					/>
				</div>
			</div>
		</div>

		{#if filteredItems === null || loading}
			<div class="w-full h-full flex justify-center items-center my-16 mb-24">
				<Spinner className="size-5" />
			</div>
		{:else if (filteredItems ?? []).length !== 0}
			<div class="my-1">
				<div class="grid gap-y-0.5">
					{#each filteredItems as skill (skill.id)}
						<div
							class="group flex min-h-8 w-full cursor-pointer items-center gap-2 overflow-hidden rounded-xl px-2 py-1 text-left"
							role="button"
							tabindex="0"
							on:click={(e) => {
								if (shouldIgnoreRowClick(e.target)) return;
								openSkill(skill);
							}}
							on:keydown={(e) => {
								if (e.currentTarget !== e.target) return;
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									openSkill(skill);
								}
							}}
						>
							<div class="min-w-0 flex-1 py-2">
								<div class="truncate text-sm font-medium">{skill.name}</div>
								<div class="mt-0.5 truncate text-xs text-gray-500">{skill.description ?? ''}</div>
							</div>
							{#if !skill.write_access}
								<Badge type="muted" content={$i18n.t('Read Only')} />
							{/if}
							<div class="shrink-0" data-skill-toggle>
								{#if skill.write_access}
									<Switch
										state={skill.is_active}
										ariaLabel={skill.name}
										on:change={async () => {
											try {
												const updated = await toggleSkillById(localStorage.token, skill.id);
												skill.is_active = updated.is_active;
												filteredItems = [...filteredItems];
												_skills.set(await getSkills(localStorage.token));
											} catch (error) {
												toast.error(`${error}`);
												await loadSkillItems();
											}
										}}
									/>
								{:else}
									<span class="text-xs text-gray-500"
										>{skill.is_active ? $i18n.t('Enabled') : $i18n.t('Disabled')}</span
									>
								{/if}
							</div>

							{#if skill.write_access}
								<div class="ml-2 flex shrink-0 flex-row items-center self-center">
									{#if shiftKey}
										<Tooltip content={$i18n.t('Delete')}>
											<button
												class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition dark:text-gray-500"
												type="button"
												aria-label={$i18n.t('Delete')}
												on:click={(e) => {
													e.preventDefault();
													e.stopPropagation();
													deleteHandler(skill);
												}}
											>
												<GarbageBin className="size-4" />
											</button>
										</Tooltip>
									{:else}
										<div class="flex shrink-0 flex-row items-center gap-1.5 self-center">
											<SkillMenu
												exportBundleHandler={() => exportBundleHandler(skill)}
												show={openSkillMenuId === skill.id}
												editHandler={() => {
													goto(`/workspace/skills/edit?id=${encodeURIComponent(skill.id)}`);
												}}
												cloneHandler={() => {
													cloneHandler(skill);
												}}
												exportHandler={() => {
													exportHandler(skill);
												}}
												deleteHandler={async () => {
													selectedSkill = skill;
													showDeleteConfirm = true;
												}}
												onClose={() => {
													openSkillMenuId = null;
												}}
											>
												<button
													class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition dark:text-gray-500"
													type="button"
													aria-label={$i18n.t('Skill Menu')}
													on:click={(e) => {
														e.preventDefault();
														e.stopPropagation();
														openSkillMenuId = openSkillMenuId === skill.id ? null : skill.id;
													}}
												>
													<EllipsisHorizontal className="size-4" />
												</button>
											</SkillMenu>
										</div>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			</div>

			{#if total > 30}
				<div class="flex justify-center mt-4 mb-2">
					<Pagination bind:page count={total} perPage={30} />
				</div>
			{/if}
		{:else}
			<div class="flex w-full flex-col items-center justify-center py-16 pb-24">
				<div class="max-w-sm text-center text-gray-900 dark:text-gray-100">
					<div class="mb-1.5 text-sm">{$i18n.t('No skills found')}</div>
					<div class="text-center text-xs leading-5 text-gray-500">
						{$i18n.t('Try adjusting your search or filter to find what you are looking for.')}
					</div>
				</div>
			</div>
		{/if}
	</div>

	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		title={$i18n.t('Delete skill?')}
		on:confirm={() => {
			deleteHandler(selectedSkill);
		}}
	>
		<div class=" text-sm text-gray-500 truncate">
			{$i18n.t('This will delete')} <span class="  font-normal">{selectedSkill.name}</span>.
		</div>
	</DeleteConfirmDialog>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner className="size-5" />
	</div>
{/if}
