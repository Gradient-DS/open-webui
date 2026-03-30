<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { verify2FA } from '$lib/apis/auths';

	const i18n = getContext('i18n');

	export let partialToken: string;
	export let onSuccess: (sessionUser: any) => void;
	export let onCancel: () => void;

	let code = '';
	let loading = false;
	let useRecoveryCode = false;

	const handleSubmit = async () => {
		if (loading) return;

		const trimmed = code.trim();
		if (!trimmed) return;

		loading = true;

		const sessionUser = await verify2FA(partialToken, trimmed).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		loading = false;

		if (sessionUser) {
			onSuccess(sessionUser);
		}
	};

	const handleCodeInput = (e: Event) => {
		const target = e.target as HTMLInputElement;
		const value = target.value;

		// Auto-submit when 6 digits entered for TOTP mode
		if (!useRecoveryCode && /^\d{6}$/.test(value)) {
			code = value;
			handleSubmit();
		}
	};
</script>

<div class="w-full">
	<div class="mb-1">
		<div class="text-2xl font-medium">
			{$i18n.t('Two-Factor Authentication')}
		</div>
		<div class="mt-1 text-sm text-gray-500 dark:text-gray-400">
			{#if useRecoveryCode}
				{$i18n.t('Enter one of your recovery codes')}
			{:else}
				{$i18n.t('Enter the 6-digit code from your authenticator app')}
			{/if}
		</div>
	</div>

	<form
		class="flex flex-col mt-4"
		on:submit|preventDefault={handleSubmit}
	>
		<div class="mb-4">
			{#if useRecoveryCode}
				<input
					bind:value={code}
					type="text"
					class="w-full text-sm outline-hidden bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-600 uppercase tracking-widest text-center font-mono"
					placeholder="XXXXX-XXXXX"
					autocomplete="off"
					required
				/>
			{:else}
				<input
					bind:value={code}
					on:input={handleCodeInput}
					type="text"
					inputmode="numeric"
					pattern="\d{6}"
					maxlength="6"
					class="w-full text-sm outline-hidden bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-600 text-center tracking-[0.5em] font-mono text-lg"
					placeholder="000000"
					autocomplete="one-time-code"
					autofocus
					required
				/>
			{/if}
		</div>

		<div>
			<button
				class="w-full px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
				type="submit"
				disabled={loading}
			>
				{#if loading}
					{$i18n.t('Verifying...')}
				{:else}
					{$i18n.t('Verify')}
				{/if}
			</button>
		</div>

		<div class="mt-3 flex justify-between items-center">
			<button
				class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
				type="button"
				on:click={() => {
					useRecoveryCode = !useRecoveryCode;
					code = '';
				}}
			>
				{#if useRecoveryCode}
					{$i18n.t('Use authenticator code')}
				{:else}
					{$i18n.t('Use a recovery code')}
				{/if}
			</button>

			<button
				class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
				type="button"
				on:click={onCancel}
			>
				{$i18n.t('Back')}
			</button>
		</div>
	</form>
</div>
