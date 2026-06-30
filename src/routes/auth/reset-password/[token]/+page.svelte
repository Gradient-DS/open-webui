<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { validatePasswordResetToken, resetPassword } from '$lib/apis/auths';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { WEBUI_NAME } from '$lib/stores';
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

<svelte:head>
	<title>{$i18n.t('Reset password')} | {$WEBUI_NAME}</title>
</svelte:head>

<div class="w-full h-screen max-h-[100dvh] text-white relative">
	<div class="w-full h-full absolute top-0 left-0 bg-white dark:bg-black"></div>

	<div
		class="fixed bg-transparent min-h-screen w-full flex justify-center font-primary z-50 text-black dark:text-white"
	>
		<div class="w-full px-10 min-h-screen flex flex-col text-center">
			<div class="my-auto flex flex-col justify-center items-center">
				<div class="sm:max-w-md my-auto pb-10 w-full dark:text-gray-100">
					<div class="flex justify-center mb-6">
						<img
							crossorigin="anonymous"
							src="{WEBUI_BASE_URL}/static/favicon.png"
							class="size-24 rounded-full"
							alt=""
						/>
					</div>

					{#if state === 'loading'}
						<div class="flex items-center justify-center gap-3 text-xl">
							<div>{$i18n.t('Loading...')}</div>
							<Spinner className="size-5" />
						</div>
					{:else if state === 'valid'}
						<form class="flex flex-col justify-center" on:submit|preventDefault={submitHandler}>
							<div class="mb-1">
								<div class="text-2xl font-medium">
									{$i18n.t('Reset password')}
								</div>
							</div>

							<div class="flex flex-col mt-4">
								<div class="mb-2">
									<label for="new-password" class="text-sm font-medium text-left mb-1 block">
										{$i18n.t('New password')}
									</label>
									<SensitiveInput
										id="new-password"
										bind:value={password}
										placeholder={$i18n.t('New password')}
										required={true}
									/>
								</div>

								<div class="mb-2">
									<label for="confirm-password" class="text-sm font-medium text-left mb-1 block">
										{$i18n.t('Confirm password')}
									</label>
									<SensitiveInput
										id="confirm-password"
										bind:value={confirmPassword}
										placeholder={$i18n.t('Confirm password')}
										required={true}
									/>
								</div>
							</div>

							<div class="mt-5">
								<button
									type="submit"
									class="w-full text-sm font-medium text-center text-white bg-gray-900 dark:bg-white dark:text-gray-900 rounded-lg py-2.5 hover:bg-gray-800 dark:hover:bg-gray-100 transition disabled:opacity-50 disabled:cursor-not-allowed"
									disabled={submitting}
								>
									{#if submitting}
										<Spinner className="size-4 inline mr-1" />
									{/if}
									{$i18n.t('Reset password')}
								</button>
							</div>
						</form>
					{:else if state === 'done'}
						<div class="text-xl font-medium mb-2">
							{$i18n.t('Reset password')}
						</div>
						<p class="text-gray-500 dark:text-gray-400 mb-4">
							{$i18n.t('Your password has been reset. You can now sign in.')}
						</p>
						<a href="/auth" class="text-sm text-blue-600 dark:text-blue-400 hover:underline">
							{$i18n.t('Back to sign in')}
						</a>
					{:else}
						<div class="text-xl font-medium mb-2">
							{$i18n.t('Reset link invalid or expired')}
						</div>
						<p class="text-gray-500 dark:text-gray-400 mb-4">
							{$i18n.t('This password reset link is invalid or has expired. Please request a new one.')}
						</p>
						<a href="/auth" class="text-sm text-blue-600 dark:text-blue-400 hover:underline">
							{$i18n.t('Back to sign in')}
						</a>
					{/if}
				</div>
			</div>
		</div>
	</div>
</div>
