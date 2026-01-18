<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	import XMark from '$lib/components/icons/XMark.svelte';
	import AdvancedParams from '../Settings/Advanced/AdvancedParams.svelte';
	import Valves from '$lib/components/chat/Controls/Valves.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';

	import { user, settings } from '$lib/stores';
	import { isFeatureEnabled, isChatControlSectionEnabled } from '$lib/utils/features';
	export let models = [];
	export let chatFiles = [];
	export let params = {};

	let showValves = false;

	// Collect knowledge items from all selected models
	$: modelKnowledge = models.reduce((acc, model) => {
		const knowledge = model?.info?.meta?.knowledge ?? [];
		if (knowledge.length > 0) {
			return [...acc, { modelName: model.name, items: knowledge }];
		}
		return acc;
	}, []);
</script>

<div class=" dark:text-white">
	<div class=" flex items-center justify-between dark:text-gray-100 mb-2">
		<div class=" text-lg font-medium self-center font-primary">{$i18n.t('Chat Controls')}</div>
		<button
			class="self-center"
			on:click={() => {
				dispatch('close');
			}}
		>
			<XMark className="size-3.5" />
		</button>
	</div>

	{#if isFeatureEnabled('chat_controls') && ($user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true))}
		<div class=" dark:text-gray-200 text-sm font-primary py-0.5 px-0.5">
			{#if isChatControlSectionEnabled('files')}
				<Collapsible title={$i18n.t('Files')} open={true} buttonClassName="w-full">
					<div class="flex flex-col gap-1 mt-1.5" slot="content">
						{#if chatFiles.length > 0}
							{#each chatFiles as file, fileIdx}
								<FileItem
									className="w-full"
									item={file}
									edit={true}
									url={file?.url ? file.url : null}
									name={file.name}
									type={file.type}
									size={file?.size}
									dismissible={true}
									small={true}
									on:dismiss={() => {
										// Remove the file from the chatFiles array

										chatFiles.splice(fileIdx, 1);
										chatFiles = chatFiles;
									}}
									on:click={() => {
										console.log(file);
									}}
								/>
							{/each}
						{/if}

						{#if modelKnowledge.length > 0}
							{#each modelKnowledge as { modelName, items }}
								<div class="mt-2">
									<div class="text-xs text-gray-500 dark:text-gray-400 mb-1">
										{$i18n.t('From model')}: {modelName}
									</div>
									{#each items as item}
										<FileItem
											className="w-full"
											item={item}
											edit={false}
											url={item?.url ? item.url : null}
											name={item.name}
											type={item?.type ?? 'collection'}
											size={item?.size}
											dismissible={false}
											small={true}
										/>
									{/each}
								</div>
							{/each}
						{/if}

						{#if chatFiles.length === 0 && modelKnowledge.length === 0}
							<div class="text-xs text-gray-500 dark:text-gray-400 py-2">
								{$i18n.t('Files will appear here when attached to the chat.')}
							</div>
						{/if}
					</div>
				</Collapsible>

				<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
			{/if}

			{#if isChatControlSectionEnabled('valves') && ($user?.role === 'admin' || ($user?.permissions.chat?.valves ?? true))}
				<Collapsible bind:open={showValves} title={$i18n.t('Valves')} buttonClassName="w-full">
					<div class="text-sm" slot="content">
						<Valves show={showValves} />
					</div>
				</Collapsible>

				<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
			{/if}

			{#if isChatControlSectionEnabled('system_prompt') && isFeatureEnabled('system_prompt')}
				{#if $user?.role === 'admin' || ($user?.permissions.chat?.system_prompt ?? true)}
					<Collapsible title={$i18n.t('System Prompt')} open={true} buttonClassName="w-full">
						<div class="" slot="content">
							<textarea
								bind:value={params.system}
								class="w-full text-xs outline-hidden resize-vertical {$settings.highContrastMode
									? 'border-2 border-gray-300 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800 p-2.5'
									: 'py-1.5 bg-transparent'}"
								rows="4"
								placeholder={$i18n.t('Enter system prompt')}
							/>
						</div>
					</Collapsible>

					<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
				{/if}
			{/if}

			{#if isChatControlSectionEnabled('params') && ($user?.role === 'admin' || ($user?.permissions.chat?.params ?? true))}
				<Collapsible title={$i18n.t('Advanced Params')} open={true} buttonClassName="w-full">
					<div class="text-sm mt-1.5" slot="content">
						<div>
							<AdvancedParams admin={$user?.role === 'admin'} custom={true} bind:params />
						</div>
					</div>
				</Collapsible>
			{/if}
		</div>
	{/if}
</div>
