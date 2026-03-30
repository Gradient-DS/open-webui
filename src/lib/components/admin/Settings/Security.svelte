<script lang="ts">
	import { get2FAConfig, set2FAConfig } from '$lib/apis/configs';
	import Switch from '$lib/components/common/Switch.svelte';
	import { config } from '$lib/stores';
	import { getBackendConfig } from '$lib/apis';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	let twoFAConfig = {
		ENABLE_2FA: false,
		REQUIRE_2FA: false,
		TWO_FA_GRACE_PERIOD_DAYS: 7
	};

	const updateHandler = async () => {
		const res = await set2FAConfig(localStorage.token, twoFAConfig).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		await config.set(await getBackendConfig());

		if (res) {
			saveHandler();
		} else {
			toast.error($i18n.t('Failed to update settings'));
		}
	};

	onMount(async () => {
		const res = await get2FAConfig(localStorage.token).catch(() => null);
		if (res) twoFAConfig = res;
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={updateHandler}
>
	<div class="overflow-y-scroll scrollbar-hidden h-full">
		<div class="space-y-3 pr-1.5">
			<div>
				<div class="text-base font-medium">{$i18n.t('Two-Factor Authentication')}</div>
				<div class="text-xs text-gray-500 mt-0.5">
					{$i18n.t('Manage two-factor authentication settings for all users.')}
				</div>
			</div>

			<div class="space-y-3">
				<div class="flex w-full justify-between pr-2">
					<div class="self-center text-xs font-medium">
						{$i18n.t('Enable Two-Factor Authentication')}
					</div>
					<Switch bind:state={twoFAConfig.ENABLE_2FA} />
				</div>

				{#if twoFAConfig.ENABLE_2FA}
					<div class="flex w-full justify-between pr-2">
						<div class="self-center text-xs font-medium">
							{$i18n.t('Require 2FA for All Users')}
						</div>
						<Switch bind:state={twoFAConfig.REQUIRE_2FA} />
					</div>

					{#if twoFAConfig.REQUIRE_2FA}
						<div class="w-full pr-2">
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Grace Period (days)')}
							</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								type="number"
								min="0"
								max="90"
								bind:value={twoFAConfig.TWO_FA_GRACE_PERIOD_DAYS}
							/>
							<div class="text-xs text-gray-500 mt-1">
								{$i18n.t('Users will have this many days to set up 2FA after enforcement is enabled.')}
							</div>
						</div>
					{/if}
				{/if}
			</div>
		</div>
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
