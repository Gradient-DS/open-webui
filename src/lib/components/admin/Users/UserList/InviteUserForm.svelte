<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, getContext } from 'svelte';

	import { addUser } from '$lib/apis/auths';
	import { createInvite } from '$lib/apis/invites';
	import { config } from '$lib/stores';
	import { generateInitialsImage } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let loading = false;

	type CreationMode = 'email' | 'link' | 'password';
	let mode: CreationMode = $config?.features?.enable_email_invites ? 'email' : 'password';

	let _user = {
		name: '',
		email: '',
		password: '',
		role: 'user'
	};

	// Copy link dialog state
	let showCopyDialog = false;
	let inviteUrl = '';
	let emailSent = false;

	export function reset() {
		_user = { name: '', email: '', password: '', role: 'user' };
		mode = $config?.features?.enable_email_invites ? 'email' : 'password';
		showCopyDialog = false;
		inviteUrl = '';
		emailSent = false;
	}

	const graphConfigured = () => {
		// Email mode requires Graph API configuration
		return $config?.features?.enable_email_invites ?? false;
	};

	const submitHandler = async () => {
		loading = true;

		try {
			if (mode === 'password') {
				// Existing flow: create user with password
				const res = await addUser(
					localStorage.token,
					_user.name,
					_user.email,
					_user.password,
					_user.role,
					generateInitialsImage(_user.name)
				);

				if (res) {
					dispatch('save');
					dispatch('close');
				}
			} else {
				// Invite flow: create invite, optionally send email
				const sendEmail = mode === 'email';
				const res = await createInvite(
					localStorage.token,
					_user.name,
					_user.email,
					_user.role,
					sendEmail
				);

				if (res) {
					inviteUrl = res.invite_url;
					emailSent = res.email_sent;

					if (mode === 'email') {
						if (emailSent) {
							toast.success(
								$i18n.t('Email invite sent to {{email}}', { email: _user.email })
							);
						} else {
							toast.warning(
								$i18n.t('Invite created but email could not be sent. You can copy the link instead.')
							);
							showCopyDialog = true;
						}
						dispatch('save');
						if (!showCopyDialog) {
							dispatch('close');
						}
					} else {
						// Copy Link mode: show dialog
						showCopyDialog = true;
						dispatch('save');
					}
				}
			}
		} catch (error) {
			toast.error(`${error}`);
		}

		loading = false;
	};

	const copyToClipboard = async () => {
		try {
			await navigator.clipboard.writeText(inviteUrl);
			toast.success($i18n.t('Invite link copied to clipboard'));
		} catch {
			// Fallback
			const input = document.createElement('input');
			input.value = inviteUrl;
			document.body.appendChild(input);
			input.select();
			document.execCommand('copy');
			document.body.removeChild(input);
			toast.success($i18n.t('Invite link copied to clipboard'));
		}
	};
</script>

{#if showCopyDialog}
	<!-- Copy Link Dialog -->
	<div class="px-1">
		<div class="mb-3">
			<div class="text-sm font-medium mb-2">{$i18n.t('Invite Created')}</div>
			<p class="text-xs text-gray-500 mb-3">
				{$i18n.t('Share this link with the user:')}
			</p>
			<div class="flex items-center gap-2 p-2 bg-gray-50 dark:bg-gray-900 rounded-lg">
				<input
					type="text"
					value={inviteUrl}
					readonly
					class="flex-1 text-xs bg-transparent outline-none truncate"
				/>
				<button
					type="button"
					class="shrink-0 px-2.5 py-1 text-xs font-medium bg-black dark:bg-white text-white dark:text-black rounded-md hover:bg-gray-900 dark:hover:bg-gray-100 transition"
					on:click={copyToClipboard}
				>
					{$i18n.t('Copy link')}
				</button>
			</div>
			<p class="text-xs text-gray-400 mt-2">
				{$i18n.t('This link expires in {{hours}} hours.', {
					hours: String($config?.features?.invite_expiry_hours ?? 168)
				})}
			</p>
		</div>

		<div class="flex justify-end pt-3 text-sm font-medium">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
				type="button"
				on:click={() => {
					showCopyDialog = false;
					dispatch('close');
				}}
			>
				{$i18n.t('Done')}
			</button>
		</div>
	</div>
{:else}
	<!-- Creation Mode Selector -->
	<div class="flex gap-1.5 mb-3 -mt-1">
		<button
			type="button"
			class="flex-1 px-2 py-1.5 text-xs font-medium rounded-lg border transition {mode === 'email'
				? 'border-gray-900 dark:border-white bg-gray-900 dark:bg-white text-white dark:text-black'
				: 'border-gray-200 dark:border-gray-700 hover:border-gray-400 dark:hover:border-gray-500'}"
			on:click={() => (mode = 'email')}
			disabled={!graphConfigured()}
			title={!graphConfigured() ? $i18n.t('Email sending not configured') : ''}
		>
			{$i18n.t('Send Email Invite')}
		</button>
		<button
			type="button"
			class="flex-1 px-2 py-1.5 text-xs font-medium rounded-lg border transition {mode === 'link'
				? 'border-gray-900 dark:border-white bg-gray-900 dark:bg-white text-white dark:text-black'
				: 'border-gray-200 dark:border-gray-700 hover:border-gray-400 dark:hover:border-gray-500'}"
			on:click={() => (mode = 'link')}
		>
			{$i18n.t('Copy Invite Link')}
		</button>
		<button
			type="button"
			class="flex-1 px-2 py-1.5 text-xs font-medium rounded-lg border transition {mode === 'password'
				? 'border-gray-900 dark:border-white bg-gray-900 dark:bg-white text-white dark:text-black'
				: 'border-gray-200 dark:border-gray-700 hover:border-gray-400 dark:hover:border-gray-500'}"
			on:click={() => (mode = 'password')}
		>
			{$i18n.t('Set Password')}
		</button>
	</div>

	<!-- Form Fields -->
	<div class="px-1">
		<div class="flex flex-col w-full mb-3">
			<div class="mb-1 text-xs text-gray-500">{$i18n.t('Role')}</div>
			<div class="flex-1">
				<select
					class="dark:bg-gray-900 w-full capitalize rounded-lg text-sm bg-transparent dark:disabled:text-gray-500 outline-hidden"
					bind:value={_user.role}
					placeholder={$i18n.t('Enter Your Role')}
					required
				>
					<option value="pending">{$i18n.t('pending')}</option>
					<option value="user">{$i18n.t('user')}</option>
					<option value="admin">{$i18n.t('admin')}</option>
				</select>
			</div>
		</div>

		<div class="flex flex-col w-full mt-1">
			<div class="mb-1 text-xs text-gray-500">{$i18n.t('Name')}</div>
			<div class="flex-1">
				<input
					class="w-full text-sm bg-transparent disabled:text-gray-500 dark:disabled:text-gray-500 outline-hidden"
					type="text"
					bind:value={_user.name}
					placeholder={$i18n.t('Full name')}
					autocomplete="off"
					required
				/>
			</div>
		</div>

		<hr class="border-gray-100/30 dark:border-gray-850/30 my-2.5 w-full" />

		<div class="flex flex-col w-full">
			<div class="mb-1 text-xs text-gray-500">{$i18n.t('Email')}</div>
			<div class="flex-1">
				<input
					class="w-full text-sm bg-transparent disabled:text-gray-500 dark:disabled:text-gray-500 outline-hidden"
					type="email"
					bind:value={_user.email}
					placeholder={$i18n.t('Email address')}
					required
				/>
			</div>
		</div>

		{#if mode === 'password'}
			<div class="flex flex-col w-full mt-1">
				<div class="mb-1 text-xs text-gray-500">{$i18n.t('Password')}</div>
				<div class="flex-1">
					<SensitiveInput
						class="w-full text-sm bg-transparent disabled:text-gray-500 dark:disabled:text-gray-500 outline-hidden"
						type="password"
						bind:value={_user.password}
						placeholder={$i18n.t('Password')}
						autocomplete="off"
						required
					/>
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex flex-row space-x-1 items-center {loading
				? ' cursor-not-allowed'
				: ''}"
			type="submit"
			disabled={loading}
			on:click|preventDefault={submitHandler}
		>
			{#if mode === 'email'}
				{$i18n.t('Send Invite')}
			{:else if mode === 'link'}
				{$i18n.t('Create Invite')}
			{:else}
				{$i18n.t('Save')}
			{/if}

			{#if loading}
				<div class="ml-2 self-center">
					<Spinner />
				</div>
			{/if}
		</button>
	</div>
{/if}
