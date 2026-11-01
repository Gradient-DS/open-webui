<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { getChatById } from '$lib/apis/chats';
	import type { ProjectionPage } from '$lib/apis/threads';
	import { user } from '$lib/stores';
	import {
		sharingAPI,
		shareStateText,
		personAudience,
		groupAudience,
		type ShareAudience,
		type ThreadShare
	} from '$lib/utils/threadSharing';
	import { copyToClipboard } from '$lib/utils';
	import Modal from '../common/Modal.svelte';
	import MemberSelector from '../workspace/common/MemberSelector.svelte';

	export let chatId: string;
	export let show = false;
	const i18n = getContext<Readable<I18n>>('i18n');
	let shares: ThreadShare[] = [];
	let userIds: string[] = [];
	let groupIds: string[] = [];
	let selectedUsers: Record<
		string,
		{ id: string; name: string; email: string; person_id?: string | null }
	> = {};
	let at = 0;
	let error = '';
	let busy = true;
	let loaded = false;
	let threadId = '';
	const api = sharingAPI(localStorage.token ?? '');
	$: audiences = [
		...userIds.map((id) => personAudience(selectedUsers[id])),
		...groupIds.map((id) => groupAudience({ id }))
	].filter((audience): audience is ShareAudience => audience !== null);
	const unknownNames = () => ({
		person: $i18n.t('Unavailable person'),
		group: $i18n.t('Unavailable group'),
		folder: $i18n.t('Unavailable folder')
	});

	onMount(async () => {
		try {
			const page: ProjectionPage = await getChatById(localStorage.token, chatId, '', chatId);
			threadId = page.thread_id;
			at = Math.max(0, ...page.messages.filter((m) => m.finished).map((m) => m.position));
			shares = (await api.list(chatId)).items;
			loaded = true;
		} catch (e) {
			error = shareStateText(e);
		} finally {
			busy = false;
		}
	});

	const change = async (audiences: ShareAudience[], remove = false) => {
		if (busy || !loaded || (!remove && !at)) return;
		busy = true;
		error = '';
		try {
			for (const audience of audiences) {
				if (remove) await api.unshare(chatId, audience);
				else {
					await api.share(chatId, { audience, at });
					userIds = userIds.filter((id) => selectedUsers[id]?.person_id !== audience.id);
					groupIds = groupIds.filter((id) => groupAudience({ id })?.id !== audience.id);
				}
			}
		} catch (e) {
			error = shareStateText(e);
		} finally {
			try {
				shares = (await api.list(chatId)).items;
			} catch (e) {
				error = shareStateText(e);
			}
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
		<form class="flex flex-col gap-2" on:submit|preventDefault={() => change(audiences)}>
			<div class="text-sm font-medium">{$i18n.t('Share with')}</div>
			<MemberSelector
				bind:userIds
				bind:groupIds
				bind:selectedUsers
				platformPeople={true}
				includeUsers={($user?.permissions?.access_grants?.allow_users ?? true) ||
					$user?.role === 'admin'}
				pagination={true}
				disabled={busy || !loaded}
			/>
			<button class="rounded border p-2" disabled={busy || !loaded || !at || !audiences.length}
				>{$i18n.t('Share Chat')}</button
			>
		</form>
		{#each shares as share (share.audience.kind + ':' + share.audience.id)}
			<div class="flex justify-between gap-2 text-sm">
				<span>{share.name || unknownNames()[share.audience.kind]}</span>
				<button disabled={busy} on:click={() => change([share.audience], true)}
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
