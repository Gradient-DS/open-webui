<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { getEmailConfig, setEmailConfig, testEmailConfig, getInviteContent, setInviteContent } from '$lib/apis/configs';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	let loading = true;
	let saving = false;
	let testing = false;

	let ENABLE_EMAIL_INVITES = false;
	let EMAIL_FROM_ADDRESS = '';
	let EMAIL_FROM_NAME = '';
	let INVITE_EXPIRY_HOURS = 168;

	let inviteSubject = '';
	let inviteHeading = '';

	onMount(async () => {
		try {
			const config = await getEmailConfig(localStorage.token);
			if (config) {
				ENABLE_EMAIL_INVITES = config.ENABLE_EMAIL_INVITES;
				EMAIL_FROM_ADDRESS = config.EMAIL_FROM_ADDRESS;
				EMAIL_FROM_NAME = config.EMAIL_FROM_NAME;
				INVITE_EXPIRY_HOURS = config.INVITE_EXPIRY_HOURS;
			}

			const inviteContent = await getInviteContent(localStorage.token);
			inviteSubject = inviteContent?.subject ?? '';
			inviteHeading = inviteContent?.heading ?? '';
		} catch (err) {
			toast.error(`${err}`);
		}
		loading = false;
	});

	const handleSave = async () => {
		saving = true;
		try {
			await setEmailConfig(localStorage.token, {
				ENABLE_EMAIL_INVITES,
				EMAIL_FROM_ADDRESS,
				EMAIL_FROM_NAME,
				INVITE_EXPIRY_HOURS
			});
			await setInviteContent(localStorage.token, inviteSubject, inviteHeading);
			saveHandler();
		} catch (err) {
			toast.error(`${err}`);
		}
		saving = false;
	};

	const handleTestEmail = async () => {
		testing = true;
		try {
			const res = await testEmailConfig(localStorage.token);
			if (res) {
				toast.success(res.message);
			}
		} catch (err) {
			toast.error(`${err}`);
		}
		testing = false;
	};
</script>

<form
	class="flex flex-col h-full justify-between text-sm"
	on:submit|preventDefault={handleSave}
>
	<div class="overflow-y-scroll pr-1.5 max-h-[28rem]">
		{#if loading}
			<div class="flex justify-center py-8">
				<Spinner />
			</div>
		{:else}
			<div class="space-y-3">
				<!-- Enable Toggle -->
				<div class="flex justify-between items-center">
					<div class="font-medium">{$i18n.t('Email Invitations')}</div>
					<Switch bind:state={ENABLE_EMAIL_INVITES} />
				</div>

				{#if ENABLE_EMAIL_INVITES}
					<!-- Sender Section -->
					<div class="space-y-3 pt-2">
						<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
							{$i18n.t('Sender')}
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">{$i18n.t('From Address')}</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="email"
								bind:value={EMAIL_FROM_ADDRESS}
								placeholder="no-reply@soev.ai"
							/>
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">{$i18n.t('From Name')}</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="text"
								bind:value={EMAIL_FROM_NAME}
								placeholder="Soev"
							/>
						</div>
					</div>

					<!-- Invite Settings -->
					<div class="space-y-3 pt-4">
						<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
							{$i18n.t('Invite Settings')}
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">{$i18n.t('Expiry (hours)')}</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="number"
								bind:value={INVITE_EXPIRY_HOURS}
								min="1"
								max="8760"
							/>
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">{$i18n.t('Invite Subject')}</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="text"
								bind:value={inviteSubject}
								placeholder={$i18n.t('Leave empty to use the default')}
							/>
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">{$i18n.t('Invite Heading')}</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="text"
								bind:value={inviteHeading}
								placeholder={$i18n.t('Leave empty to use the default')}
							/>
						</div>
					</div>

				{/if}
			</div>
		{/if}
	</div>

	<div class="flex justify-between pt-3 text-sm font-medium">
		<div>
			{#if ENABLE_EMAIL_INVITES}
				<button
					class="px-3.5 py-1.5 text-sm font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 transition rounded-full flex items-center gap-1.5 {testing
						? 'cursor-not-allowed'
						: ''}"
					type="button"
					disabled={testing}
					on:click={handleTestEmail}
				>
					{$i18n.t('Test Email')}
					{#if testing}
						<Spinner className="size-3" />
					{/if}
				</button>
			{/if}
		</div>
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex items-center gap-1.5 {saving
				? 'cursor-not-allowed'
				: ''}"
			type="submit"
			disabled={saving}
		>
			{$i18n.t('Save')}
			{#if saving}
				<Spinner className="size-3" />
			{/if}
		</button>
	</div>
</form>
