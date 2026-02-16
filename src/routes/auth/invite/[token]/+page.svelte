<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { toast } from 'svelte-sonner';

	import { validateInvite, acceptInvite } from '$lib/apis/invites';
	import { getBackendConfig } from '$lib/apis';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { WEBUI_NAME, config, user, socket } from '$lib/stores';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	const i18n = getContext('i18n');

	let state: 'loading' | 'valid' | 'expired' | 'accepted' | 'invalid' = 'loading';
	let invite: {
		email: string;
		name: string;
		role: string;
		invited_by_name: string;
		expires_at: number;
	} | null = null;

	let name = '';
	let password = '';
	let confirmPassword = '';
	let submitting = false;

	const token = $page.params.token;

	onMount(async () => {
		try {
			const result = await validateInvite(token);
			if (result) {
				invite = result;
				name = result.name;
				state = 'valid';
			}
		} catch (error) {
			const errorStr = String(error);
			if (errorStr.includes('expired')) {
				state = 'expired';
			} else if (errorStr.includes('already been used')) {
				state = 'accepted';
			} else if (errorStr.includes('revoked')) {
				state = 'expired';
			} else {
				state = 'invalid';
			}
		}
	});

	const submitHandler = async () => {
		if (password !== confirmPassword) {
			toast.error($i18n.t('Passwords do not match'));
			return;
		}

		if (password.length < 8) {
			toast.error($i18n.t('Password must be at least 8 characters'));
			return;
		}

		submitting = true;

		try {
			const sessionUser = await acceptInvite(token, password, name !== invite?.name ? name : undefined);

			if (sessionUser) {
				toast.success($i18n.t(`You're now logged in.`));
				localStorage.token = sessionUser.token;
				$socket?.emit('user-join', { auth: { token: sessionUser.token } });
				await user.set(sessionUser);
				await config.set(await getBackendConfig());
				goto('/');
			}
		} catch (error) {
			toast.error(String(error));
		} finally {
			submitting = false;
		}
	};
</script>

<svelte:head>
	<title>{$i18n.t('Accept Invite')} | {$WEBUI_NAME}</title>
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
					{:else if state === 'valid' && invite}
						<form
							class="flex flex-col justify-center"
							on:submit|preventDefault={submitHandler}
						>
							<div class="mb-1">
								<div class="text-2xl font-medium">
									{#if $config?.client_name}
										{$i18n.t("You've been invited to the soev.ai environment of {{clientName}}", { clientName: $config.client_name })}
									{:else}
										{$i18n.t("You've been invited to join")}
									{/if}
								</div>
								<div class="mt-1 text-sm text-gray-500 dark:text-gray-400">
									{$i18n.t('Invited by {{name}}', { name: invite.invited_by_name })}
								</div>
							</div>

							<div class="flex flex-col mt-4">
								<div class="mb-2">
									<label for="name" class="text-sm font-medium text-left mb-1 block">
										{$i18n.t('Name')}
									</label>
									<input
										bind:value={name}
										type="text"
										id="name"
										class="my-0.5 w-full text-sm outline-hidden bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-600"
										autocomplete="name"
										required
									/>
								</div>

								<div class="mb-2">
									<label for="email" class="text-sm font-medium text-left mb-1 block">
										{$i18n.t('Email')}
									</label>
									<input
										value={invite.email}
										type="email"
										id="email"
										class="my-0.5 w-full text-sm outline-hidden bg-transparent text-gray-500"
										disabled
									/>
								</div>

								<hr class="my-3 dark:border-gray-800" />

								<div class="mb-2">
									<label for="password" class="text-sm font-medium text-left mb-1 block">
										{$i18n.t('Password')}
									</label>
									<SensitiveInput
										id="password"
										bind:value={password}
										placeholder={$i18n.t('Enter the password for your new account')}
										required={true}
									/>
								</div>

								<div class="mb-2">
									<label for="confirm-password" class="text-sm font-medium text-left mb-1 block">
										{$i18n.t('Confirm Password')}
									</label>
									<SensitiveInput
										id="confirm-password"
										bind:value={confirmPassword}
										placeholder={$i18n.t('Confirm Password')}
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
									{$i18n.t('Accept Invite')}
								</button>
							</div>
						</form>
					{:else if state === 'expired'}
						<div class="text-xl font-medium mb-2">
							{$i18n.t('Invite expired')}
						</div>
						<p class="text-gray-500 dark:text-gray-400">
							{$i18n.t('This invite has expired. Please contact your administrator.')}
						</p>
					{:else if state === 'accepted'}
						<div class="text-xl font-medium mb-2">
							{$i18n.t('Invite has already been accepted')}
						</div>
						<p class="text-gray-500 dark:text-gray-400 mb-4">
							{$i18n.t('This invite has already been used.')}
						</p>
						<a
							href="/auth"
							class="text-sm text-blue-600 dark:text-blue-400 hover:underline"
						>
							{$i18n.t('Sign in')}
						</a>
					{:else}
						<div class="text-xl font-medium mb-2">
							{$i18n.t('Invalid invite link')}
						</div>
						<p class="text-gray-500 dark:text-gray-400">
							{$i18n.t('This invite link is not valid. Please check with your administrator.')}
						</p>
					{/if}
				</div>
			</div>
		</div>
	</div>
</div>
