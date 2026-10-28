<script lang="ts">
	import {
		addTagById,
		deleteTagById,
		getAllTags,
		getTagsById,
		updateChatById
	} from '$lib/apis/chats';
	import { config, tags as _tags } from '$lib/stores';
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';

	const dispatch = createEventDispatcher();
	const i18n = getContext<Readable<I18n>>('i18n');

	import Tags from '../common/Tags.svelte';
	import { toast } from 'svelte-sonner';

	export let chatId = '';
	export let disabled = false;
	let tags: { name: string }[] = [];

	const getTags = async () => {
		return await getTagsById(localStorage.token, chatId).catch(() => []);
	};

	const addTag = async (tagName: string) => {
		if (disabled) return;
		const res = await addTagById(localStorage.token, chatId, tagName).catch(async (error) => {
			toast.error($i18n.t(`${error}`));
			return null;
		});
		if (!res) {
			return;
		}

		tags = await getTags();
		if (!$config?.features?.enable_v2_threads) {
			await updateChatById(localStorage.token, chatId, { tags });
		}

		await _tags.set(await getAllTags(localStorage.token));
		dispatch('add', {
			name: tagName
		});
	};

	const deleteTag = async (tagName: string) => {
		if (disabled) return;
		const res = await deleteTagById(localStorage.token, chatId, tagName).catch((error) => {
			toast.error($i18n.t(`${error}`));
			return null;
		});
		if (!res) return;
		tags = await getTags();
		if (!$config?.features?.enable_v2_threads) {
			await updateChatById(localStorage.token, chatId, { tags });
		}

		await _tags.set(await getAllTags(localStorage.token));
		dispatch('delete', {
			name: tagName
		});
	};

	onMount(async () => {
		if (chatId) {
			tags = await getTags();
		}
	});
</script>

<Tags
	{tags}
	{disabled}
	suggestionTags={$_tags ?? []}
	on:delete={(e) => {
		deleteTag(e.detail);
	}}
	on:add={(e) => {
		addTag(e.detail);
	}}
/>
