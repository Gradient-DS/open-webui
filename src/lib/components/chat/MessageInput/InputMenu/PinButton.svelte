<script lang="ts">
	// [Gradient] Pins a "+" menu item to the composer bar; persisted in user settings.
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { getContext } from 'svelte';

	import { settings } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Pin from '$lib/components/icons/Pin.svelte';
	import PinSlash from '$lib/components/icons/PinSlash.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let itemId: string;

	$: pinned = ($settings?.pinnedInputItems ?? []).includes(itemId);

	const togglePin = async () => {
		const pinnedItems = $settings?.pinnedInputItems ?? [];
		settings.set({
			...$settings,
			pinnedInputItems: pinnedItems.includes(itemId)
				? pinnedItems.filter((id) => id !== itemId)
				: [...new Set([...pinnedItems, itemId])]
		});
		await updateUserSettings(localStorage.token, { ui: $settings });
	};
</script>

<Tooltip content={pinned ? $i18n.t('Unpin') : $i18n.t('Pin')} className="flex shrink-0">
	<button
		type="button"
		class="p-0.5 rounded-md text-gray-500 hover:text-gray-800 hover:bg-gray-100 dark:text-gray-400 dark:hover:text-gray-100 dark:hover:bg-gray-700 transition-colors"
		aria-label={pinned ? $i18n.t('Unpin') : $i18n.t('Pin')}
		aria-pressed={pinned}
		on:click|stopPropagation|preventDefault={togglePin}
	>
		{#if pinned}
			<PinSlash className="size-3.5" />
		{:else}
			<Pin className="size-3.5" />
		{/if}
	</button>
</Tooltip>
