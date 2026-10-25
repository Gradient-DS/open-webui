<script lang="ts">
	import type { Token } from 'marked';
	import { LinkPreview } from 'bits-ui';

	import { getContext } from 'svelte';

	import { goto } from '$app/navigation';
	import { models } from '$lib/stores';
	import UserStatusLinkPreview from '$lib/components/common/UserStatusLinkPreview.svelte';

	const i18n = getContext('i18n');

	export let token: Token;

	let triggerChar = '';
	let label = '';

	let idType = null;
	let id = '';

	$: if (token) {
		init();
	}

	const init = () => {
		const _id = token?.id;
		triggerChar = token?.triggerChar ?? '@';

		if (triggerChar === '$') {
			// Skill mention: id format is "skillId|label"
			const pipeIdx = _id?.indexOf('|') ?? -1;
			if (pipeIdx > 0) {
				id = _id.substring(0, pipeIdx);
				label = _id.substring(pipeIdx + 1);
			} else {
				id = _id;
				label = _id;
			}
			idType = null;
			return;
		}

		// split by : and take first part as idType and second part as id
		const parts = _id?.split(':');
		if (parts) {
			idType = parts[0];
			id = parts.slice(1).join(':'); // in case id contains ':'
		} else {
			idType = null;
			id = _id;
		}

		label = token?.label ?? id;

		if (triggerChar === '@') {
			if (idType === 'U') {
				// User
			} else if (idType === 'M') {
				// Model
				const model = $models.find((m) => m.id === id);
				if (model) {
					label = model.name;
				} else {
					label = $i18n.t('Unknown');
				}
			}
		}
	};
</script>

<LinkPreview.Root openDelay={0} closeDelay={0}>
	<LinkPreview.Trigger class=" cursor-pointer no-underline! font-normal! ">
		<!-- svelte-ignore a11y-click-events-have-key-events -->
		<!-- svelte-ignore a11y-no-static-element-interactions -->

		<span
			class="mention"
			on:click={async () => {
				if (triggerChar === '@') {
					if (idType === 'U') {
						// Open user profile
						console.log('Clicked user mention', id);
					} else if (idType === 'M') {
						console.log('Clicked model mention', id);
						await goto(`/?model=${id}`);
					}
				} else {
					// Unknown trigger char, just log
					console.log('Clicked mention', id);
				}
			}}
		>
			{triggerChar}{label}
		</span>
	</LinkPreview.Trigger>

	{#if triggerChar === '@' && idType === 'U'}
		<UserStatusLinkPreview {id} />
	{/if}
</LinkPreview.Root>
