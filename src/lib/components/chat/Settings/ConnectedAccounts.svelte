<script lang="ts">
	import { getContext, onMount, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { toast } from 'svelte-sonner';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import * as cloudSync from '$lib/apis/cloudSync';
	import type { Connection } from '$lib/apis/cloudSync';
	import Badge from '$lib/components/common/Badge.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import UserSettingSection from './UserSettingSection.svelte';
	import DisconnectAccountDialog from './DisconnectAccountDialog.svelte';
	import {
		CLOUD_PROVIDERS,
		connectResult,
		trustedConnectOrigins,
		connectionOutcome
	} from '$lib/components/workspace/Knowledge/utils/cloudSync';

	const i18n = getContext<Writable<I18n>>('i18n');
	let accounts: Connection[] = [];
	let usage: Record<string, number | null> = {};
	let loading = true;
	let loadError = false;
	let busy = false;
	let destroyed = false;
	let disconnectTarget: Connection | null = null;
	let showDisconnect = false;
	let cancelAuthorization: (() => void) | undefined;
	const provider = (account: Connection) =>
		CLOUD_PROVIDERS[account.source_kind]?.label ?? account.source_kind;
	const status = (account: Connection) =>
		account.last_error
			? 'Needs reconnect'
			: account.lifecycle === 'pending'
				? 'Pending'
				: account.lifecycle === 'enabled'
					? 'Connected'
					: 'Needs reconnect';

	async function refresh() {
		loadError = false;
		try {
			const connections = await cloudSync.listConnections(localStorage.token);
			const visible = connections.filter((account) => account.lifecycle !== 'revoked');
			const counts = await Promise.all(
				visible.map(async (account) => {
					try {
						const result = await cloudSync.getConnectionUsage(localStorage.token, account.id);
						return [account.id, result.knowledge_ids.length] as const;
					} catch {
						return [account.id, null] as const;
					}
				})
			);
			if (destroyed) return;
			accounts = visible;
			usage = Object.fromEntries(counts);
		} catch {
			if (!destroyed) loadError = true;
		} finally {
			if (!destroyed) loading = false;
		}
	}

	function reconnect(account: Connection) {
		if (busy) return;
		const popup = window.open('about:blank', 'soev_connect', 'width=600,height=700,scrollbars=yes');
		if (!popup) {
			toast.error($i18n.t('Please allow popups to connect your account.'));
			return;
		}
		busy = true;
		let finished = false;
		let checking = false;
		let ready = false;
		const authorizationStartedAt = Date.now();
		const finish = () => {
			if (finished) return;
			finished = true;
			clearInterval(poll);
			clearTimeout(timeout);
			window.removeEventListener('message', handleMessage);
			popup.close();
			cancelAuthorization = undefined;
			busy = false;
			if (!destroyed) void refresh();
		};
		const check = async () => {
			if (checking || finished || !ready) return;
			checking = true;
			try {
				const connection = await cloudSync.getConnection(localStorage.token, account.id);
				if (finished) return;
				accounts = accounts.map((item) => (item.id === connection.id ? connection : item));
				const outcome = connectionOutcome(connection, Date.now() - authorizationStartedAt);
				if (outcome.status === 'done') finish();
				else if (outcome.status === 'failed' || outcome.status === 'gave_up') {
					toast.error($i18n.t('Authorization was not completed.'));
					finish();
				}
			} catch {
				if (!finished) {
					toast.error($i18n.t('Failed to check connected accounts.'));
					finish();
				}
			} finally {
				checking = false;
			}
		};
		const trustedOrigins = trustedConnectOrigins(window.location.origin, WEBUI_API_BASE_URL);
		const handleMessage = (event: MessageEvent) => {
			const result = connectResult(event, trustedOrigins, popup, account.id);
			if (result === 'pending') void check();
			else if (result === 'error' || result === 'invalid') {
				toast.error($i18n.t('Authorization failed'));
				finish();
			}
		};
		window.addEventListener('message', handleMessage);
		const poll = setInterval(() => void check(), 3000);
		const timeout = setTimeout(() => {
			toast.error($i18n.t('Authorization timed out. Please try again.'));
			finish();
		}, 120000);
		cancelAuthorization = finish;
		void cloudSync
			.authorizeConnection(localStorage.token, account.id)
			.then((authorization) => {
				if (finished) return;
				popup.location.href = authorization.authorize_url;
				ready = true;
			})
			.catch(() => {
				if (!finished) {
					toast.error($i18n.t('Authorization failed'));
					finish();
				}
			});
	}

	async function disconnect() {
		const target = disconnectTarget;
		disconnectTarget = null;
		if (!target || busy) return;
		busy = true;
		try {
			await cloudSync.revokeConnection(localStorage.token, target.id);
			if (!destroyed) await refresh();
		} catch {
			if (!destroyed) toast.error($i18n.t('Failed to disconnect account.'));
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		void refresh();
	});
	onDestroy(() => {
		destroyed = true;
		cancelAuthorization?.();
	});
</script>

<UserSettingSection title={$i18n.t('Connected accounts')}>
	{#if loading}<Spinner className="size-4" />
	{:else if loadError}
		<p role="alert" class="text-xs text-red-600">
			{$i18n.t('Failed to check connected accounts.')}
		</p>
		<button type="button" class="self-start text-xs underline" on:click={refresh}
			>{$i18n.t('Retry loading accounts')}</button
		>
	{:else if !accounts.length}<p class="text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t('No connected accounts yet. Add a source in a knowledge base to connect.')}
		</p>
	{:else}
		{#each accounts as account (account.id)}
			<div
				class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-100 p-3 dark:border-gray-800"
			>
				<div class="min-w-0 space-y-1">
					<div class="flex flex-wrap items-center gap-2">
						<span class="text-sm">{$i18n.t(provider(account))}</span><Badge
							content={$i18n.t(status(account))}
							type={status(account) === 'Connected'
								? 'success'
								: status(account) === 'Pending'
									? 'muted'
									: 'error'}
						/>
					</div>
					<p class="text-xs text-gray-500 dark:text-gray-400">
						{#if usage[account.id] !== null && usage[account.id] !== undefined}{$i18n.t(
								'Knowledge bases that use this account: {{n}}',
								{ n: usage[account.id] }
							)}
						{:else}{$i18n.t('Could not check knowledge base usage.')}{/if}
					</p>
				</div>
				<div class="flex gap-3 text-xs">
					<button
						type="button"
						class="underline disabled:opacity-50"
						disabled={busy}
						on:click={() => reconnect(account)}>{$i18n.t('Reconnect')}</button
					>
					<button
						type="button"
						class="underline disabled:opacity-50"
						disabled={busy}
						on:click={() => {
							disconnectTarget = account;
							showDisconnect = true;
						}}>{$i18n.t('Disconnect')}</button
					>
				</div>
			</div>
		{/each}
	{/if}
</UserSettingSection>

<DisconnectAccountDialog
	bind:show={showDisconnect}
	provider={disconnectTarget ? provider(disconnectTarget) : ''}
	on:confirm={disconnect}
	on:cancel={() => {
		disconnectTarget = null;
	}}
/>
