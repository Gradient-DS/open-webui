<script lang="ts">
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';
	import TwoFactorSetup from '$lib/components/chat/Settings/Account/TwoFactorSetup.svelte';

	const i18n = getContext('i18n');

	export let show = false;

	let setupComplete = false;

	$: gracePeriodDays = $config?.features?.two_fa_grace_period_days ?? 0;
	$: canDismiss = gracePeriodDays > 0;
</script>

{#if show && !setupComplete}
	<div class="fixed w-full h-full flex z-999">
		<div
			class="absolute w-full h-full backdrop-blur-lg bg-white/10 dark:bg-gray-900/50 flex justify-center items-center p-4"
		>
			<div class="flex flex-col max-w-md w-full">
				<div class="text-center dark:text-white text-2xl font-medium mb-1">
					{$i18n.t('Two-Factor Authentication Required')}
				</div>

				<div class="text-center text-sm text-gray-500 dark:text-gray-400 mb-6">
					{$i18n.t('Your administrator requires two-factor authentication for all accounts.')}
				</div>

				<div
					class="bg-white dark:bg-gray-900 rounded-2xl p-6 shadow-lg border border-gray-100 dark:border-gray-800"
				>
					<TwoFactorSetup
						on:enabled={() => {
							setupComplete = true;
							show = false;
						}}
					/>
				</div>

				{#if canDismiss}
					<div class="mt-4 text-center">
						<button
							class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
							on:click={() => {
								show = false;
							}}
						>
							{$i18n.t('Set up later')}
							<span class="text-gray-400">
								({$i18n.t('{{days}} days remaining', { days: gracePeriodDays })})
							</span>
						</button>
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}
