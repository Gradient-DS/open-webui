<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { getChatById } from '$lib/apis/chats';
	import type { ProjectionPage } from '$lib/apis/threads';
	import {
		sharingAPI,
		shareStateText,
		type ShareAudience,
		type ThreadShare
	} from '$lib/utils/threadSharing';
	import { copyToClipboard } from '$lib/utils';
	import Modal from '../common/Modal.svelte';

	export let chatId: string;
	export let show = false;
	const i18n = getContext<Readable<I18n>>('i18n');
	let shares: ThreadShare[] = [];
	let kind: ShareAudience['kind'] = 'person';
	let audienceId = '';
	let at = 0;
	let error = '';
	let busy = true;
	let threadId = '';
	const api = sharingAPI(localStorage.token ?? '');
	const labels = () => ({
		person: $i18n.t('Person'),
		group: $i18n.t('Platform group'),
		folder: $i18n.t('Shared folder')
	});

	onMount(async () => {
		try {
			const page: ProjectionPage = await getChatById(localStorage.token, chatId);
			threadId = page.thread_id;
			at = Math.max(0, ...page.messages.filter((m) => m.finished).map((m) => m.position));
			shares = (await api.list(chatId)).items;
		} catch (e) {
			error = shareStateText(e);
		} finally {
			busy = false;
		}
	});

	const change = async (audience: ShareAudience, remove = false) => {
		busy = true;
		error = '';
		try {
			if (remove) await api.unshare(chatId, audience);
			else await api.share(chatId, { audience, at });
			shares = (await api.list(chatId)).items;
		} catch (e) {
			error = shareStateText(e);
		} finally {
			busy = false;
		}
	};
</script>

<Modal bind:show size="md">
	<div class="p-5 space-y-4">
		<h2 class="font-medium">{$i18n.t('Share Chat')}</h2>
		<p class="text-sm">
			{$i18n.t(
				'Only signed-in people in your organisation who can access the sources can read this chat.'
			)}
		</p>
		{#if error}<p role="alert">{$i18n.t(error)}</p>{/if}
		<form
			class="flex flex-col gap-2"
			on:submit|preventDefault={() => change({ kind, id: audienceId.trim() })}
		>
			<label
				>{$i18n.t('Share with')}
				<select class="w-full bg-transparent rounded border p-2" bind:value={kind}>
					{#each Object.entries(labels()) as [value, label]}<option {value}>{label}</option>{/each}
				</select>
			</label>
			<label
				>{$i18n.t('Audience identifier')}
				<input
					class="w-full bg-transparent rounded border p-2"
					bind:value={audienceId}
					required
					maxlength="128"
				/>
			</label>
			<button class="rounded border p-2" disabled={busy || !at || !audienceId.trim()}
				>{$i18n.t('Share Chat')}</button
			>
		</form>
		{#each shares as share}
			<div class="flex justify-between gap-2 text-sm">
				<span>{labels()[share.audience.kind]}: {share.audience.id}</span>
				<button disabled={busy} on:click={() => change(share.audience, true)}
					>{$i18n.t('Unshare')}</button
				>
			</div>
		{/each}
		<div class="flex justify-end gap-4">
			<button
				disabled={!shares.length || busy}
				on:click={() => copyToClipboard(`${window.location.origin}/s/${threadId}`)}
				>{$i18n.t('Copy Link')}</button
			>
			<button on:click={() => (show = false)}>{$i18n.t('Close')}</button>
		</div>
	</div>
</Modal>
