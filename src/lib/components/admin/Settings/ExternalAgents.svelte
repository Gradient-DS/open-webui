<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';

	import {
		getExternalAgentsConfig,
		setExternalAgentsConfig
	} from '$lib/apis/configs';

	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: () => void = () => {};

	let loaded = false;
	let agentApiEnabled = false;
	let agents: string[] = [];
	let selectedAgent = '';

	const loadConfig = async () => {
		try {
			const cfg = await getExternalAgentsConfig(localStorage.token);
			if (cfg) {
				agentApiEnabled = cfg.AGENT_API_ENABLED ?? false;
				agents = Array.isArray(cfg.AGENT_API_AGENTS) ? cfg.AGENT_API_AGENTS : [];
				selectedAgent = cfg.AGENT_API_SELECTED_AGENT ?? '';
			}
		} catch (err) {
			toast.error($i18n.t('Failed to load external agents configuration'));
		} finally {
			loaded = true;
		}
	};

	const save = async () => {
		if (!agentApiEnabled) {
			return;
		}
		if (agents.length > 0 && !agents.includes(selectedAgent)) {
			toast.error($i18n.t('Selected agent is not in the configured list'));
			return;
		}
		const res = await setExternalAgentsConfig(localStorage.token, selectedAgent).catch(() => null);
		if (res) {
			saveHandler();
		} else {
			toast.error($i18n.t('Failed to save agent selection'));
		}
	};

	onMount(() => {
		loadConfig();
	});
</script>

<form
	class="flex flex-col h-full justify-between text-sm"
	on:submit|preventDefault={save}
>
	<div class="overflow-y-scroll scrollbar-hidden h-full">
		{#if !loaded}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{:else}
			<div class="mb-3">
				<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('External Agents')}</div>

				{#if !agentApiEnabled}
					<div
						class="p-3 rounded-lg bg-yellow-50 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-200 text-xs"
					>
						{$i18n.t(
							'Agent API is disabled. Set AGENT_API_ENABLED=true to use external agents.'
						)}
					</div>
				{:else if agents.length === 0}
					<div
						class="p-3 rounded-lg bg-gray-50 dark:bg-gray-850 text-gray-700 dark:text-gray-300 text-xs"
					>
						{$i18n.t(
							'No external agents configured. Set AGENT_API_AGENTS in your environment.'
						)}
					</div>
				{:else}
					<div class="text-xs text-gray-500 mb-3">
						{$i18n.t('Select the active external agent used for chat completions.')}
					</div>

					<div class="mb-2.5 flex flex-col w-full">
						<label class="font-medium mb-1" for="external-agent-select">
							{$i18n.t('Active agent')}
						</label>
						<select
							id="external-agent-select"
							class="w-full rounded-lg text-sm bg-transparent outline-hidden border border-gray-100 dark:border-gray-850 px-3 py-1.5"
							bind:value={selectedAgent}
						>
							{#each agents as agent}
								<option value={agent}>{agent}</option>
							{/each}
						</select>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	{#if loaded && agentApiEnabled && agents.length > 0}
		<div class="flex justify-end pt-3 text-sm font-medium">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
				type="submit"
			>
				{$i18n.t('Save')}
			</button>
		</div>
	{/if}
</form>
