<script lang="ts">
	import { getContext } from 'svelte';

	import { settings } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';

	import Modal from '$lib/components/common/Modal.svelte';
	import AdjustmentsHorizontal from '$lib/components/icons/AdjustmentsHorizontal.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';

	const i18n = getContext('i18n');

	export let show = false;

	let saving = false;

	const choose = async (advanced: boolean) => {
		if (saving) return;
		saving = true;

		await settings.set({ ...$settings, advancedMode: advanced, modePromptSeen: true });
		await updateUserSettings(localStorage.token, { ui: $settings });

		saving = false;
		show = false;
	};
</script>

<Modal bind:show size="sm">
	<div class="px-6 py-5 dark:text-white text-black">
		<div class="text-center">
			<h2 class="text-xl font-medium m-0">
				{$i18n.t('How would you like to start?')}
			</h2>
			<p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
				{$i18n.t('Welcome to {{name}}', { name: 'soev.ai' })}
			</p>
		</div>

		<div class="flex flex-col gap-3 mt-5">
			<button
				class="flex items-start gap-3 text-left rounded-2xl p-4 border border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800 transition disabled:opacity-50"
				type="button"
				disabled={saving}
				on:click={() => choose(false)}
			>
				<div class="self-center shrink-0 text-gray-700 dark:text-gray-200">
					<Sparkles className="size-5" strokeWidth="2" />
				</div>
				<div class="min-w-0">
					<div class="font-medium">{$i18n.t('Basic')}</div>
					<div class="text-sm text-gray-500 dark:text-gray-400">
						{$i18n.t('A clean, simple interface with just the essentials.')}
					</div>
				</div>
			</button>

			<button
				class="flex items-start gap-3 text-left rounded-2xl p-4 border border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800 transition disabled:opacity-50"
				type="button"
				disabled={saving}
				on:click={() => choose(true)}
			>
				<div class="self-center shrink-0 text-gray-700 dark:text-gray-200">
					<AdjustmentsHorizontal className="size-5" strokeWidth="2" />
				</div>
				<div class="min-w-0">
					<div class="font-medium">{$i18n.t('Advanced')}</div>
					<div class="text-sm text-gray-500 dark:text-gray-400">
						{$i18n.t('All features, including agents, prompts, tools and more.')}
					</div>
				</div>
			</button>
		</div>

		<p class="text-xs text-center text-gray-500 dark:text-gray-400 mt-4">
			{$i18n.t('You can switch anytime via your profile menu → Advanced Mode.')}
		</p>
	</div>
</Modal>
