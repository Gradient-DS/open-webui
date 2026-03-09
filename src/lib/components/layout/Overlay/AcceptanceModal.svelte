<script lang="ts">
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';
	import { getContext } from 'svelte';
	import { config, user, settings } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';

	const i18n = getContext('i18n');

	export let show = false;

	const getAcceptanceHash = async (title: string, content: string) => {
		const text = `${title}:${content}`;
		const encoder = new TextEncoder();
		const data = encoder.encode(text);
		const hashBuffer = await crypto.subtle.digest('SHA-256', data);
		const hashArray = Array.from(new Uint8Array(hashBuffer));
		return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
	};

	const acceptHandler = async () => {
		const hash = await getAcceptanceHash(
			$config?.ui?.acceptance_modal_title ?? '',
			$config?.ui?.acceptance_modal_content ?? ''
		);

		await settings.set({ ...$settings, acceptance_hash: hash });
		await updateUserSettings(localStorage.token, { ui: $settings });
		show = false;
	};
</script>

{#if show}
	<div class="fixed w-full h-full flex z-999">
		<div
			class="absolute w-full h-full backdrop-blur-lg bg-white/10 dark:bg-gray-900/50 flex justify-center"
		>
			<div class="m-auto pb-10 flex flex-col justify-center">
				<div class="max-w-md">
					<div
						class="text-center dark:text-white text-2xl font-medium z-50"
						style="white-space: pre-wrap;"
					>
						{#if ($config?.ui?.acceptance_modal_title ?? '').trim() !== ''}
							{$config.ui.acceptance_modal_title}
						{:else}
							{$i18n.t('Terms of Use')}
						{/if}
					</div>

					<div
						class="mt-4 text-center text-sm dark:text-gray-200 w-full prose dark:prose-invert prose-sm"
					>
						{#if ($config?.ui?.acceptance_modal_content ?? '').trim() !== ''}
							{@html DOMPurify.sanitize(
								marked.parse($config?.ui?.acceptance_modal_content ?? '')
							)}
						{:else}
							{$i18n.t('Please accept the terms of use to continue.')}
						{/if}
					</div>

					<div class="mt-6 mx-auto relative group w-fit">
						<button
							class="relative z-20 flex px-5 py-2 rounded-full bg-black dark:bg-white text-white dark:text-black hover:bg-gray-900 dark:hover:bg-gray-100 transition font-medium text-sm"
							on:click={acceptHandler}
						>
							{#if ($config?.ui?.acceptance_modal_button_text ?? '').trim() !== ''}
								{$config.ui.acceptance_modal_button_text}
							{:else}
								{$i18n.t('I Accept')}
							{/if}
						</button>
					</div>
				</div>
			</div>
		</div>
	</div>
{/if}
