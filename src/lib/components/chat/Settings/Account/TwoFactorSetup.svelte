<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		get2FAStatus,
		setup2FATOTP,
		enable2FATOTP,
		disable2FATOTP,
		regenerateRecoveryCodes
	} from '$lib/apis/auths';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Badge from '$lib/components/common/Badge.svelte';

	const i18n = getContext('i18n');

	// States: 'loading' | 'not_enrolled' | 'setup' | 'recovery_codes' | 'enrolled'
	let state: string = 'loading';

	// Status
	let totpEnabled = false;
	let recoveryCodesRemaining = 0;

	// Setup flow
	let qrCodeBase64 = '';
	let secret = '';
	let verificationCode = '';
	let setupPassword = '';

	// Recovery codes (shown once after enabling or regenerating)
	let recoveryCodes: string[] = [];
	let savedCodesConfirmed = false;

	// Disable flow
	let disablePassword = '';
	let showDisable = false;

	// Regenerate flow
	let regenPassword = '';
	let showRegen = false;

	let loading = false;

	const loadStatus = async () => {
		const res = await get2FAStatus(localStorage.token).catch(() => null);
		if (res) {
			totpEnabled = res.totp_enabled;
			recoveryCodesRemaining = res.recovery_codes_remaining;
			state = totpEnabled ? 'enrolled' : 'not_enrolled';
		} else {
			state = 'not_enrolled';
		}
	};

	const startSetup = async () => {
		loading = true;
		const res = await setup2FATOTP(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		loading = false;

		if (res) {
			qrCodeBase64 = res.qr_code_base64;
			secret = res.secret;
			state = 'setup';
		}
	};

	const enableHandler = async () => {
		if (!verificationCode || !setupPassword) return;

		loading = true;
		const res = await enable2FATOTP(
			localStorage.token,
			setupPassword,
			secret,
			verificationCode
		).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		loading = false;

		if (res) {
			toast.success($i18n.t('Two-Factor Authentication Enabled'));
			recoveryCodes = res.recovery_codes;
			totpEnabled = true;
			state = 'recovery_codes';
		}
	};

	const disableHandler = async () => {
		if (!disablePassword) return;

		loading = true;
		const res = await disable2FATOTP(localStorage.token, disablePassword).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		loading = false;

		if (res) {
			toast.success($i18n.t('Two-factor authentication has been disabled.'));
			totpEnabled = false;
			disablePassword = '';
			showDisable = false;
			state = 'not_enrolled';
		}
	};

	const regenerateHandler = async () => {
		if (!regenPassword) return;

		loading = true;
		const res = await regenerateRecoveryCodes(localStorage.token, regenPassword).catch(
			(error) => {
				toast.error(`${error}`);
				return null;
			}
		);
		loading = false;

		if (res) {
			recoveryCodes = res.recovery_codes;
			regenPassword = '';
			showRegen = false;
			state = 'recovery_codes';
		}
	};

	const downloadRecoveryCodes = () => {
		const text = recoveryCodes.join('\n');
		const blob = new Blob([text], { type: 'text/plain' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = 'recovery-codes.txt';
		a.click();
		URL.revokeObjectURL(url);
	};

	onMount(() => {
		loadStatus();
	});
</script>

<div class="flex flex-col text-sm">
	{#if state === 'loading'}
		<div class="text-gray-500">{$i18n.t('Loading...')}</div>
	{:else if state === 'not_enrolled'}
		<div class="flex justify-between items-center">
			<div>
				<div class="font-medium">{$i18n.t('Two-Factor Authentication')}</div>
				<div class="text-xs text-gray-500 mt-0.5">
					{$i18n.t('Add an extra layer of security with an authenticator app')}
				</div>
			</div>
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
				type="button"
				disabled={loading}
				on:click={startSetup}
			>
				{$i18n.t('Set Up')}
			</button>
		</div>
	{:else if state === 'setup'}
		<div>
			<div class="font-medium mb-3">{$i18n.t('Set Up Two-Factor Authentication')}</div>

			<div class="text-xs text-gray-500 mb-3">
				{$i18n.t('Scan this QR code with your authenticator app')}
			</div>

			{#if qrCodeBase64}
				<div class="flex justify-center mb-3">
					<img
						src={qrCodeBase64}
						alt="TOTP QR Code"
						class="w-48 h-48 rounded-lg border border-gray-200 dark:border-gray-700"
					/>
				</div>
			{/if}

			<div class="mb-3">
				<div class="text-xs text-gray-500 mb-1">{$i18n.t('Or enter this key manually:')}</div>
				<div
					class="font-mono text-xs bg-gray-50 dark:bg-gray-850 p-2 rounded-lg select-all break-all"
				>
					{secret}
				</div>
			</div>

			<form on:submit|preventDefault={enableHandler}>
				<div class="space-y-2">
					<div>
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Password')}</div>
						<SensitiveInput
							class="w-full bg-transparent text-sm dark:text-gray-300 outline-hidden placeholder:opacity-30"
							type="password"
							bind:value={setupPassword}
							placeholder={$i18n.t('Enter your password')}
							autocomplete="current-password"
							required
						/>
					</div>

					<div>
						<div class="text-xs text-gray-500 mb-1">
							{$i18n.t('Verification Code')}
						</div>
						<input
							bind:value={verificationCode}
							type="text"
							inputmode="numeric"
							pattern="\d{6}"
							maxlength="6"
							class="w-full text-sm outline-hidden bg-transparent tracking-[0.3em] font-mono placeholder:opacity-30"
							placeholder="000000"
							autocomplete="one-time-code"
							required
						/>
					</div>
				</div>

				<div class="mt-3 flex gap-2 justify-end">
					<button
						class="px-3.5 py-1.5 text-sm font-medium text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition rounded-full"
						type="button"
						on:click={() => {
							state = 'not_enrolled';
							secret = '';
							qrCodeBase64 = '';
							setupPassword = '';
							verificationCode = '';
						}}
					>
						{$i18n.t('Cancel')}
					</button>
					<button
						class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
						type="submit"
						disabled={loading}
					>
						{$i18n.t('Enable')}
					</button>
				</div>
			</form>
		</div>
	{:else if state === 'recovery_codes'}
		<div>
			<div class="font-medium mb-1">{$i18n.t('Recovery Codes')}</div>
			<div class="text-xs text-yellow-600 dark:text-yellow-400 mb-3">
				{$i18n.t('Save these codes in a safe place. They will not be shown again.')}
			</div>

			<div
				class="grid grid-cols-2 gap-1.5 font-mono text-xs bg-gray-50 dark:bg-gray-850 p-3 rounded-lg mb-3"
			>
				{#each recoveryCodes as code}
					<div class="select-all">{code}</div>
				{/each}
			</div>

			<div class="flex items-center gap-2 mb-3">
				<button
					class="px-3 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 border border-gray-200 dark:border-gray-700 rounded-full transition"
					type="button"
					on:click={downloadRecoveryCodes}
				>
					{$i18n.t('Download')}
				</button>
			</div>

			<div class="flex items-center gap-2 mb-3">
				<input
					type="checkbox"
					id="saved-codes"
					bind:checked={savedCodesConfirmed}
					class="rounded"
				/>
				<label for="saved-codes" class="text-xs text-gray-600 dark:text-gray-400">
					{$i18n.t("I've saved these codes")}
				</label>
			</div>

			<div class="flex justify-end">
				<button
					class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50"
					type="button"
					disabled={!savedCodesConfirmed}
					on:click={() => {
						recoveryCodes = [];
						savedCodesConfirmed = false;
						loadStatus();
					}}
				>
					{$i18n.t('Done')}
				</button>
			</div>
		</div>
	{:else if state === 'enrolled'}
		<div>
			<div class="flex justify-between items-center">
				<div class="flex items-center gap-2">
					<span class="font-medium">{$i18n.t('Two-Factor Authentication')}</span>
					<Badge type="success">{$i18n.t('Enabled')}</Badge>
				</div>
			</div>

			<div class="text-xs text-gray-500 mt-1">
				{$i18n.t('{{count}} recovery codes remaining', {
					count: recoveryCodesRemaining
				})}
			</div>

			<div class="mt-3 space-y-2">
				<!-- Regenerate recovery codes -->
				<div>
					<button
						class="text-xs text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition"
						type="button"
						on:click={() => {
							showRegen = !showRegen;
							showDisable = false;
						}}
					>
						{$i18n.t('Regenerate Recovery Codes')}
					</button>

					{#if showRegen}
						<form class="mt-2 flex gap-2 items-end" on:submit|preventDefault={regenerateHandler}>
							<div class="flex-1">
								<div class="text-xs text-gray-500 mb-1">{$i18n.t('Password')}</div>
								<SensitiveInput
									class="w-full bg-transparent text-sm dark:text-gray-300 outline-hidden placeholder:opacity-30"
									type="password"
									bind:value={regenPassword}
									placeholder={$i18n.t('Enter your password')}
									autocomplete="current-password"
									required
								/>
							</div>
							<button
								class="px-3 py-1.5 text-xs font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
								type="submit"
								disabled={loading}
							>
								{$i18n.t('Regenerate')}
							</button>
						</form>
					{/if}
				</div>

				<!-- Disable 2FA -->
				<div>
					<button
						class="text-xs text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-300 transition"
						type="button"
						on:click={() => {
							showDisable = !showDisable;
							showRegen = false;
						}}
					>
						{$i18n.t('Disable Two-Factor Authentication')}
					</button>

					{#if showDisable}
						<form class="mt-2 flex gap-2 items-end" on:submit|preventDefault={disableHandler}>
							<div class="flex-1">
								<div class="text-xs text-gray-500 mb-1">{$i18n.t('Password')}</div>
								<SensitiveInput
									class="w-full bg-transparent text-sm dark:text-gray-300 outline-hidden placeholder:opacity-30"
									type="password"
									bind:value={disablePassword}
									placeholder={$i18n.t('Enter your password')}
									autocomplete="current-password"
									required
								/>
							</div>
							<button
								class="px-3 py-1.5 text-xs font-medium bg-red-600 hover:bg-red-700 text-white transition rounded-full"
								type="submit"
								disabled={loading}
							>
								{$i18n.t('Disable')}
							</button>
						</form>
					{/if}
				</div>
			</div>
		</div>
	{/if}
</div>
