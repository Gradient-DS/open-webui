<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { getLogoutRedirectUrl, userSignOut } from '$lib/apis/auths';
	import { completeSessionSignOut } from '$lib/apis/session';

	const i18n = getContext<Readable<I18n>>('i18n');
	let signingOut = $state(false);

	const signOut = async () => {
		signingOut = true;
		let redirect: string | undefined;
		await completeSessionSignOut(
			async () => {
				redirect = (await userSignOut())?.redirect_url;
			},
			() => {
				localStorage.removeItem('token');
				document.cookie = 'token=; Max-Age=0; path=/';
				window.location.replace(getLogoutRedirectUrl(redirect));
			}
		);
	};
</script>

<main
	id="main-content"
	class="flex min-h-screen items-center justify-center bg-white px-6 text-gray-900 dark:bg-gray-900 dark:text-gray-100"
>
	<div class="max-w-md text-center">
		<h1 class="text-xl font-medium">
			{$i18n.t('You do not have access yet. Ask your administrator to add you.')}
		</h1>
		<button
			class="mt-6 rounded-lg bg-gray-900 px-5 py-2 text-white disabled:opacity-50 dark:bg-white dark:text-gray-900"
			disabled={signingOut}
			onclick={signOut}>{$i18n.t('Sign Out')}</button
		>
	</div>
</main>
