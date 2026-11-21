<script lang="ts">
	// [Gradient] Council information search (raadsinformatie): the admin switches it on and picks the
	// organizations it covers; none searches every organization.
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import {
		getCouncilConfig,
		getCouncilSources,
		setCouncilConfig,
		type CouncilOrganization,
		type CouncilSource
	} from '$lib/apis/configs';

	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let saveHandler: () => void = () => {};

	// The most organizations one search covers; the agents report the rest as not searched.
	const SEARCHED_AT_ONCE = 5;
	const MAX_ORGANIZATIONS = 25;
	const inputClass =
		'w-full h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	let loaded = false;
	let agentApiEnabled = false;
	let enabled = false;
	let selected: CouncilOrganization[] = [];
	let sources: CouncilSource[] = [];
	let sourcesUnavailable = false;
	let query = '';

	$: known = new Set(sources.map((source) => source.key));
	$: chosen = new Set(selected.map((organization) => organization.key));
	$: matches = query.trim()
		? sources
				.filter(
					(source) =>
						!chosen.has(source.key) &&
						source.label.toLowerCase().includes(query.trim().toLowerCase())
				)
				.slice(0, 8)
		: [];

	const kindLabel = (kind: string) =>
		({
			gemeente: $i18n.t('Municipality'),
			provincie: $i18n.t('Province'),
			waterschap: $i18n.t('Water board')
		})[kind] ?? kind;

	const add = (source: CouncilSource) => {
		if (selected.length >= MAX_ORGANIZATIONS) {
			toast.error($i18n.t('Choose at most {{count}} organizations', { count: MAX_ORGANIZATIONS }));
			return;
		}
		selected = [...selected, { key: source.key, label: source.label }];
		query = '';
	};

	const remove = (key: string) => {
		selected = selected.filter((organization) => organization.key !== key);
	};

	const save = async () => {
		const res = await setCouncilConfig(localStorage.token, {
			ENABLE_COUNCIL: enabled,
			COUNCIL_ORGANIZATIONS: selected
		}).catch(() => null);
		if (res) {
			saveHandler();
		} else {
			toast.error($i18n.t('Failed to save council information settings'));
		}
	};

	onMount(async () => {
		try {
			const config = await getCouncilConfig(localStorage.token);
			agentApiEnabled = config.AGENT_API_ENABLED;
			enabled = config.ENABLE_COUNCIL;
			selected = config.COUNCIL_ORGANIZATIONS ?? [];
		} catch {
			toast.error($i18n.t('Failed to load council information settings'));
		} finally {
			loaded = true;
		}
		try {
			sources = (await getCouncilSources(localStorage.token)).sources;
		} catch {
			sourcesUnavailable = true;
		}
	});
</script>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={save}>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('Council Information')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if !loaded}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{:else if !agentApiEnabled}
			<div
				class="p-3 rounded-lg bg-yellow-50 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-200 text-xs"
			>
				{$i18n.t('Council information needs the agent API. Set AGENT_API_ENABLED=true to use it.')}
			</div>
		{:else}
			<AdminSettingSection title={$i18n.t('Council Information')} first>
				<AdminSettingRow
					label={$i18n.t('Enable council information search')}
					description={$i18n.t(
						'Let the assistant search public council meetings, documents and motions (Open Raadsinformatie). Search questions are sent to openbesluitvorming.nl without personal data.'
					)}
					let:labelId
				>
					<Switch bind:state={enabled} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if enabled}
					<AdminSettingRow
						label={$i18n.t('Organizations')}
						description={selected.length === 0
							? $i18n.t('None chosen: every municipality, province and water board is searched.')
							: selected.length > SEARCHED_AT_ONCE
								? $i18n.t(
										'One search covers at most {{count}} organizations; the rest are not searched.',
										{ count: SEARCHED_AT_ONCE }
									)
								: $i18n.t('Only these organizations are searched.')}
					>
						<div class="flex w-full max-w-sm flex-col gap-2">
							{#if selected.length > 0}
								<div class="flex flex-wrap gap-1.5">
									{#each selected as organization (organization.key)}
										<span
											class="flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs dark:bg-gray-850"
											class:text-red-600={sources.length > 0 && !known.has(organization.key)}
											title={sources.length > 0 && !known.has(organization.key)
												? $i18n.t('This organization is no longer available')
												: organization.key}
										>
											{organization.label}
											<button
												type="button"
												class="text-gray-500 hover:text-gray-900 dark:hover:text-white"
												aria-label={$i18n.t('Remove {{name}}', { name: organization.label })}
												on:click={() => remove(organization.key)}>×</button
											>
										</span>
									{/each}
								</div>
							{/if}
							{#if sourcesUnavailable}
								<div class="text-xs text-gray-500">
									{$i18n.t('The list of organizations is unavailable right now.')}
								</div>
							{:else}
								<input
									class={inputClass}
									type="search"
									bind:value={query}
									placeholder={$i18n.t('Search a municipality, province or water board')}
									aria-label={$i18n.t('Search a municipality, province or water board')}
								/>
								{#if matches.length > 0}
									<ul
										class="rounded-lg border border-gray-100 dark:border-gray-850 divide-y divide-gray-100 dark:divide-gray-850"
									>
										{#each matches as source (source.key)}
											<li>
												<button
													type="button"
													class="flex w-full justify-between px-2 py-1 text-left text-xs hover:bg-gray-50 dark:hover:bg-gray-850"
													on:click={() => add(source)}
												>
													<span>{source.label}</span>
													<span class="text-gray-500">{kindLabel(source.kind)}</span>
												</button>
											</li>
										{/each}
									</ul>
								{/if}
							{/if}
						</div>
					</AdminSettingRow>
				{/if}
			</AdminSettingSection>
		{/if}
	</div>

	{#if loaded && agentApiEnabled}
		<div class="flex justify-end pt-6 text-sm font-normal">
			<button
				class="px-3.5 py-1.5 text-sm font-normal bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
				type="submit"
			>
				{$i18n.t('Save')}
			</button>
		</div>
	{/if}
</form>
