<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { validatePasswordResetToken, resetPassword } from '$lib/apis/auths';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	const i18n = getContext('i18n');

	const token = $page.params.token;

	let state: 'loading' | 'valid' | 'invalid' | 'done' = 'loading';
	let password = '';
	let confirmPassword = '';
	let submitting = false;

	onMount(async () => {
		const res = await validatePasswordResetToken(token).catch(() => null);
		state = res?.valid ? 'valid' : 'invalid';
	});

	const submitHandler = async () => {
		if (password !== confirmPassword) {
			toast.error($i18n.t('Passwords do not match.'));
			return;
		}
		submitting = true;
		const res = await resetPassword(token, password).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		submitting = false;
		if (res) {
			state = 'done';
			toast.success($i18n.t('Your password has been reset. You can now sign in.'));
			setTimeout(() => goto('/auth'), 1500);
		}
	};
</script>

<div class="w-full h-screen flex items-center justify-center">
	<div class="w-full max-w-sm px-6">
		{#if state === 'loading'}
			<div class="flex justify-center"><Spinner /></div>
		{:else if state === 'invalid'}
			<h2 class="text-lg font-medium mb-2">{$i18n.t('Reset link invalid or expired')}</h2>
			<p class="text-sm text-gray-500 mb-4">
				{$i18n.t('This password reset link is invalid or has expired. Please request a new one.')}
			</p>
			<button class="font-medium underline text-sm" type="button" on:click={() => goto('/auth')}>
				{$i18n.t('Back to sign in')}
			</button>
		{:else if state === 'done'}
			<p class="text-sm text-gray-500">
				{$i18n.t('Your password has been reset. You can now sign in.')}
			</p>
		{:else}
			<h2 class="text-lg font-medium mb-4">{$i18n.t('Reset password')}</h2>
			<form on:submit|preventDefault={submitHandler} class="flex flex-col gap-3">
				<div>
					<label class="text-sm mb-1 block" for="new-password">{$i18n.t('New password')}</label>
					<SensitiveInput id="new-password" bind:value={password} required />
				</div>
				<div>
					<label class="text-sm mb-1 block" for="confirm-password"
						>{$i18n.t('Confirm password')}</label
					>
					<SensitiveInput id="confirm-password" bind:value={confirmPassword} required />
				</div>
				<button
					type="submit"
					disabled={submitting}
					class="bg-gray-900 text-white rounded-lg px-4 py-2 mt-2 disabled:opacity-50"
				>
					{$i18n.t('Reset password')}
				</button>
			</form>
		{/if}
	</div>
</div>
