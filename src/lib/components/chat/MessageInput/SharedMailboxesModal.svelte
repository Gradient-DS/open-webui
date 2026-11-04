<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import {
		addMailbox,
		removeMailbox,
		type MailboxList,
		type SharedMailbox
	} from '$lib/apis/cloudSync';
	import { addWithReconsent, mailboxErrorMessage } from '$lib/utils/shared-mailboxes';
	import { openMailConsent, reauthorizeMail } from '$lib/utils/live-connections';
	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');
	let {
		show = $bindable(false),
		connectionId,
		initial
	}: { show?: boolean; connectionId: string; initial: MailboxList } = $props();

	let mailboxes = $state<SharedMailbox[]>([]);
	let consented = $state(false);
	let address = $state('');
	let busy = $state(false);
	let errorMessage = $state('');
	let status = $state('');
	$effect(() => {
		mailboxes = initial.mailboxes;
		consented = initial.consented;
	});

	async function add() {
		if (busy) return;
		busy = true;
		errorMessage = '';
		status = '';
		let popup: Window | null | undefined;
		try {
			// Reserve consent in the submit gesture when the snapshot already knows it is missing.
			if (!consented) popup = openMailConsent();
			const mailbox = await addWithReconsent(
				() => addMailbox(localStorage.token, connectionId, address.trim()),
				async () => {
					consented = false;
					status = $i18n.t('Reconnecting Outlook…');
					await reauthorizeMail(localStorage.token, connectionId, popup);
					consented = true;
				}
			);
			mailboxes = [...mailboxes.filter((item) => item.address !== mailbox.address), mailbox];
			address = '';
		} catch (error) {
			errorMessage = $i18n.t(mailboxErrorMessage(error));
		} finally {
			popup?.close();
			status = '';
			busy = false;
		}
	}

	async function remove(address: string) {
		if (busy) return;
		busy = true;
		errorMessage = '';
		try {
			await removeMailbox(localStorage.token, connectionId, address);
			mailboxes = mailboxes.filter((item) => item.address !== address);
		} catch (error) {
			errorMessage = $i18n.t(mailboxErrorMessage(error));
		} finally {
			busy = false;
		}
	}
</script>

<Modal bind:show size="sm">
	<div class="px-5 py-4 text-sm">
		<div class="flex items-center justify-between gap-2 mb-2">
			<h1 class="font-medium">{$i18n.t('Shared mailboxes')}</h1>
			<button
				type="button"
				class="p-1 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
				aria-label={$i18n.t('Close modal')}
				onclick={() => (show = false)}><XMark className="size-4" /></button
			>
		</div>
		<p class="text-xs text-gray-500 mb-3">
			{$i18n.t('Only mailboxes you can already open in Outlook can be added.')}
		</p>
		<p class="text-xs text-gray-500 mb-3">
			{$i18n.t('Mail search searches your own mailbox and all mailboxes added here.')}
		</p>
		<ul class="space-y-1 max-h-48 overflow-y-auto mb-3">
			{#each mailboxes as mailbox (mailbox.address)}
				<li class="flex items-center justify-between gap-2 py-1">
					<span class="min-w-0 break-all">{mailbox.address}</span>
					<button
						type="button"
						class="shrink-0 text-xs text-gray-500 hover:text-red-600 disabled:opacity-50"
						disabled={busy}
						aria-label={$i18n.t('Remove {{address}}', { address: mailbox.address })}
						onclick={() => remove(mailbox.address)}>{$i18n.t('Remove')}</button
					>
				</li>
			{:else}
				<li class="text-xs text-gray-500">{$i18n.t('No shared mailboxes added yet.')}</li>
			{/each}
		</ul>
		<form
			onsubmit={(event) => {
				event.preventDefault();
				void add();
			}}
		>
			<label for="shared-mailbox-address" class="block text-xs mb-1">
				{$i18n.t('Shared mailbox email address')}
			</label>
			<div class="flex gap-2">
				<input
					id="shared-mailbox-address"
					type="email"
					bind:value={address}
					required
					disabled={busy}
					autocomplete="off"
					class="min-w-0 flex-1 rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent px-2 py-1.5 outline-hidden focus:border-blue-400"
				/>
				<button
					type="submit"
					disabled={busy || !address.trim()}
					class="rounded-full px-3 py-1.5 bg-black text-white dark:bg-white dark:text-black disabled:opacity-50"
					>{$i18n.t('Add')}</button
				>
			</div>
		</form>
		{#if busy}
			<div role="status" class="flex items-center gap-2 mt-3 text-xs text-gray-500">
				<Spinner className="size-3.5" />{status || $i18n.t('Updating shared mailboxes…')}
			</div>
		{/if}
		{#if errorMessage}
			<p role="alert" class="mt-3 text-xs text-red-600 dark:text-red-400">{errorMessage}</p>
		{/if}
	</div>
</Modal>
