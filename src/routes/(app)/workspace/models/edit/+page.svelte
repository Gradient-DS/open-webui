<script>
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';

	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	$: useSimpleBuilder =
		isFeatureEnabled('simple_assistant_builder') &&
		$page.url.searchParams.get('advanced') === null;

	const goToAdvanced = () => {
		const _id = $page.url.searchParams.get('id');
		goto(`/workspace/models/edit?id=${_id}&advanced=true`);
	};

	import { page } from '$app/stores';
	import { config, models, settings } from '$lib/stores';
	import { isFeatureEnabled } from '$lib/utils/features';

	import { getModelById, updateModelById } from '$lib/apis/models';
	import { getKnowledgeById } from '$lib/apis/knowledge';

	import { getModels } from '$lib/apis';
	import ModelEditor from '$lib/components/workspace/Models/ModelEditor.svelte';
	import SimpleModelEditor from '$lib/components/workspace/Models/SimpleModelEditor.svelte';

	let model = null;

	// Attach a knowledge base to the model and persist. Returns the model
	// with the KB folded into meta.knowledge (or the original on failure /
	// when already attached). Keeps the existing meta/knowledge intact.
	const attachKnowledgeBase = async (m, kbId) => {
		const kb = await getKnowledgeById(localStorage.token, kbId).catch((e) => {
			toast.error(`${e}`);
			return null;
		});
		if (!kb) return m;
		const item = { ...kb, type: 'collection' };
		const meta = m.meta ?? {};
		const existing = meta.knowledge ?? [];
		if (existing.some((k) => k.id === item.id)) return m;
		const updated = { ...m, meta: { ...meta, knowledge: [...existing, item] } };
		const res = await updateModelById(localStorage.token, updated.id, updated).catch((e) => {
			toast.error(`${e}`);
			return null;
		});
		return res ? updated : m;
	};

	onMount(async () => {
		if (!isFeatureEnabled('models')) {
			goto('/');
			return;
		}
		const _id = $page.url.searchParams.get('id');
		if (!_id) {
			goto('/workspace/models');
			return;
		}

		let loaded = await getModelById(localStorage.token, _id).catch(() => null);
		if (!loaded) {
			goto('/workspace/models');
			return;
		}
		if (!loaded?.write_access) {
			toast.error($i18n.t('You do not have permission to edit this model'));
			goto('/workspace/models');
			return;
		}

		// Return leg of the "+ Add knowledge" builder flow: attach the
		// newly created KB and persist, then strip the param so a refresh
		// doesn't re-attach. Assign `model` once — with the KB already in
		// place — so SimpleModelEditor seeds its Knowledge picker correctly
		// (its onMount reads model.meta.knowledge a single time).
		const selectKb = $page.url.searchParams.get('selectKb');
		if (selectKb) {
			loaded = await attachKnowledgeBase(loaded, selectKb);
			const url = new URL(window.location.href);
			url.searchParams.delete('selectKb');
			history.replaceState({}, '', url.toString());
		}

		model = loaded;
	});

	const onSubmit = async (modelInfo, { skipNavigate = false } = {}) => {
		const res = await updateModelById(localStorage.token, modelInfo.id, modelInfo);

		if (res) {
			await models.set(
				await getModels(
					localStorage.token,
					$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
				)
			);
			toast.success(
				useSimpleBuilder
					? $i18n.t('Assistant updated successfully')
					: $i18n.t('Model updated successfully')
			);
			if (!skipNavigate) {
				await goto('/workspace/models');
			}
		}
	};
</script>

{#if model}
<<<<<<< HEAD
	{#if useSimpleBuilder}
		<SimpleModelEditor edit={true} {model} draft={null} {onSubmit} onAdvanced={goToAdvanced} />
	{:else}
		<ModelEditor edit={true} {model} {onSubmit} />
	{/if}
=======
	<ModelEditor
		edit={true}
		{model}
		{onSubmit}
		onBack={async () => {
			await goto('/workspace/models');
		}}
	/>
>>>>>>> upstream/main
{/if}
