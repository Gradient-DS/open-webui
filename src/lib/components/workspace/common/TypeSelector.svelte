<script lang="ts">
	import { getContext } from 'svelte';

	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import Select from '$lib/components/common/Select.svelte';
	import { config } from '$lib/stores';

	const i18n = getContext('i18n');

	export let value = '';
	export let onChange: (value: string) => void = () => {};

	$: items = [
		{ value: '', label: $i18n.t('All Types') },
		{ value: 'local', label: $i18n.t('Local') },
		...($config?.features?.enable_onedrive_integration
			? [{ value: 'onedrive', label: 'OneDrive' }]
			: []),
		...Object.entries($config?.integration_providers ?? {}).map(([slug, provider]) => ({
			value: slug,
			label: ((provider as { name?: string } | null)?.name ?? slug) as string
		}))
	];
</script>

<Select
	bind:value
	{items}
	placeholder={$i18n.t('All Types')}
	triggerClass="relative w-full flex items-center gap-0.5 px-2.5 py-1.5 bg-gray-50 dark:bg-gray-850 rounded-xl"
	onChange={() => onChange(value)}
>
	<svelte:fragment slot="trigger" let:selectedLabel>
		<span
			class="inline-flex h-input px-0.5 w-full outline-hidden bg-transparent truncate placeholder-gray-400 focus:outline-hidden"
		>
			{selectedLabel}
		</span>
		<ChevronDown className="size-3.5" strokeWidth="2.5" />
	</svelte:fragment>

	<svelte:fragment slot="item" let:item let:selected>
		{item.label}
		<div class="ml-auto {selected ? '' : 'invisible'}">
			<Check />
		</div>
	</svelte:fragment>
</Select>
