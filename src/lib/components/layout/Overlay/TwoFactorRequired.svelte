<script lang="ts">
	import { getContext } from 'svelte';
	import TwoFactorSetup from '$lib/components/chat/Settings/Account/TwoFactorSetup.svelte';

	const i18n = getContext('i18n');

	export let show = false;
	// Epoch second at which the 2FA enrollment grace period ends. When null or
	// in the past the grace period has expired and the overlay cannot be
	// dismissed — the user must enroll to continue.
	export let gracePeriodExpiresAt: number | null = null;

	let setupComplete = false;

	const nowSeconds = Math.floor(Date.now() / 1000);

	$: graceExpired = gracePeriodExpiresAt == null || nowSeconds >= gracePeriodExpiresAt;
	$: canDismiss = !graceExpired;
	$: daysRemaining =
		gracePeriodExpiresAt != null
			? Math.max(0, Math.ceil((gracePeriodExpiresAt - nowSeconds) / 86400))
			: 0;
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

				{#if graceExpired}
					<div
						class="text-center text-sm text-red-600 dark:text-red-400 mb-4 -mt-2"
					>
						{$i18n.t(
							'Your grace period has expired. You must set up two-factor authentication to continue.'
						)}
					</div>
				{/if}

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
								({$i18n.t('{{days}} days remaining', { days: daysRemaining })})
							</span>
						</button>
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}
