<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';

	import { settings } from '$lib/stores';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n: Writable<any> = getContext('i18n');

	export let saveSettings: Function;

	let notificationEnabled = false;
	let notificationSound = true;

	const toggleNotifications = async () => {
		if (!notificationEnabled) {
			const permission =
				'Notification' in window ? await Notification.requestPermission() : 'denied';
			if (permission === 'granted') {
				notificationEnabled = true;
				saveSettings({ notificationEnabled });
			} else {
				toast.error(
					$i18n.t(
						'Response notifications cannot be activated as the website permissions have been denied. Please visit your browser settings to grant the necessary access.'
					)
				);
			}
		} else {
			notificationEnabled = false;
			saveSettings({ notificationEnabled });
		}
	};

	onMount(async () => {
		notificationEnabled = $settings.notificationEnabled ?? false;
		notificationSound = $settings?.notificationSound ?? true;
	});
</script>

<div id="tab-notifications" class="flex h-full flex-col text-sm">
	<div class="flex-1 min-h-0 w-full overflow-y-auto scrollbar-hover pr-1.5">
		<h2 class="mb-4 text-sm font-medium text-gray-900 dark:text-white">
			{$i18n.t('Notifications')}
		</h2>

		<div class="flex flex-col gap-2.5">
			<label class="flex cursor-pointer items-center justify-between">
				<span class="text-xs text-gray-600 dark:text-gray-400">
					{$i18n.t('Browser Notifications')}
				</span>
				<Switch
					state={notificationEnabled}
					ariaLabel={$i18n.t('Browser Notifications')}
					on:change={toggleNotifications}
				/>
			</label>
			<p class="-mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
				{$i18n.t('Allow browser notifications for completed responses.')}
			</p>

			<label class="flex cursor-pointer items-center justify-between">
				<span class="text-xs text-gray-600 dark:text-gray-400">
					{$i18n.t('Notification Sound')}
				</span>
				<Switch
					bind:state={notificationSound}
					ariaLabel={$i18n.t('Notification Sound')}
					on:change={() => {
						saveSettings({ notificationSound });
					}}
				/>
			</label>
		</div>
	</div>
</div>
