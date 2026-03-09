<script lang="ts">
	import { getAdminConfig, updateAdminConfig } from '$lib/apis/auths';
	import Switch from '$lib/components/common/Switch.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	let adminConfig = null;

	const updateHandler = async () => {
		const res = await updateAdminConfig(localStorage.token, adminConfig);

		if (res) {
			saveHandler();
		} else {
			toast.error($i18n.t('Failed to update settings'));
		}
	};

	onMount(async () => {
		adminConfig = await getAdminConfig(localStorage.token);
	});
</script>

{#if adminConfig !== null}
	<form
		class="flex flex-col h-full justify-between text-sm"
		on:submit|preventDefault={() => {
			updateHandler();
		}}
	>
		<div class="overflow-y-scroll scrollbar-hidden h-full">
			<div class="mb-2.5 flex w-full justify-between pr-2">
				<div class="self-center text-xs font-medium">
					{$i18n.t('Enable Acceptance Modal')}
				</div>
				<Switch bind:state={adminConfig.ENABLE_ACCEPTANCE_MODAL} />
			</div>

			{#if adminConfig.ENABLE_ACCEPTANCE_MODAL}
				<div class="mb-2.5">
					<div class="self-center text-xs font-medium mb-2">
						{$i18n.t('Acceptance Modal Title')}
					</div>
					<Textarea
						placeholder={$i18n.t(
							'Enter a title for the acceptance modal. Leave empty for default.'
						)}
						bind:value={adminConfig.ACCEPTANCE_MODAL_TITLE}
					/>
				</div>

				<div class="mb-2.5">
					<div class="self-center text-xs font-medium mb-2">
						{$i18n.t('Acceptance Modal Content')}
					</div>
					<Textarea
						placeholder={$i18n.t(
							'Enter content for the acceptance modal. Supports markdown. Leave empty for default.'
						)}
						bind:value={adminConfig.ACCEPTANCE_MODAL_CONTENT}
					/>
				</div>

				<div class="mb-2.5">
					<div class="self-center text-xs font-medium mb-2">
						{$i18n.t('Acceptance Modal Button Text')}
					</div>
					<Textarea
						placeholder={$i18n.t(
							'Enter button text for the acceptance modal. Leave empty for default.'
						)}
						bind:value={adminConfig.ACCEPTANCE_MODAL_BUTTON_TEXT}
					/>
				</div>
			{/if}
		</div>

		<div class="flex justify-end pt-3">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
				type="submit"
			>
				{$i18n.t('Save')}
			</button>
		</div>
	</form>
{/if}
