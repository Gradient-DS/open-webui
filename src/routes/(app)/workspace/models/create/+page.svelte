<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { config, models, settings } from '$lib/stores';
	import { isFeatureEnabled } from '$lib/utils/features';
	import { WEBUI_BASE_URL } from '$lib/constants';

	import { onMount, getContext } from 'svelte';

	onMount(() => {
		if (!isFeatureEnabled('models')) {
			goto('/');
		}
	});
	import { createNewModel, updateModelById } from '$lib/apis/models';
	import { getModels } from '$lib/apis';
	import { updateUserSettings } from '$lib/apis/users';

	import SimpleModelEditor from '$lib/components/workspace/Models/SimpleModelEditor.svelte';
	import { createdAssistant } from '$lib/stores/createdAssistant';

	const i18n = getContext('i18n');
	// [Gradient] File imports save in place before uploading, then keep editing the same assistant.
	let createdId: string | null = null;

	const onSubmit = async (modelInfo, { skipNavigate = false } = {}) => {
		if (createdId !== modelInfo.id && $models.find((m) => m.id === modelInfo.id)) {
			toast.error(
				$i18n.t(
					"Error: A model with the ID '{{modelId}}' already exists. Please select a different ID to proceed.",
					{ modelId: modelInfo.id }
				)
			);
			return false;
		}

		if (modelInfo.id === '') {
			toast.error($i18n.t('Error: Model ID cannot be empty. Please enter a valid ID to proceed.'));
			return false;
		}

		if (modelInfo) {
			const persist = (info: any) =>
				createdId === info.id
					? updateModelById(localStorage.token, info.id, info)
					: createNewModel(localStorage.token, info);
			const res = await persist({
				...modelInfo,
				meta: {
					...modelInfo.meta,
					// LICENSE covers this Open WebUI fallback logo.
					// Do not alter, remove, obscure, or replace it except as LICENSE permits:
					// https://docs.openwebui.com/license.
					profile_image_url:
						modelInfo.meta.profile_image_url ?? `${WEBUI_BASE_URL}/static/favicon.png`,
					suggestion_prompts: modelInfo.meta.suggestion_prompts
						? modelInfo.meta.suggestion_prompts.filter((prompt) => prompt.content !== '')
						: null
				},
				params: { ...modelInfo.params }
			}).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (res) {
				createdId = res.id;
				await models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
					)
				);

				// Auto-pin the newly created agent for the creator
				const pinnedModels = $settings?.pinnedModels ?? [];
				if (!pinnedModels.includes(modelInfo.id)) {
					const updatedPinned = [...new Set([...pinnedModels, modelInfo.id])];
					settings.set({ ...$settings, pinnedModels: updatedPinned });
					await updateUserSettings(localStorage.token, { ui: $settings });
				}

				toast.success($i18n.t('Assistant created successfully!'));
				// Land on the edit page for the just-created assistant, where
				// Sharing is available. The "+ Add knowledge" flow passes
				// skipNavigate so it can route to the KB-create flow itself.
				if (!skipNavigate) {
					// The creator owns it, so the edit page can show it at once.
					createdAssistant.set({ ...res, write_access: true });
					await goto(`/workspace/models/edit?id=${encodeURIComponent(modelInfo.id)}`);
				}
				return true;
			}
		}
		return false;
	};

	let model = null;

	onMount(() => {
		const handleMessageEvent = async (event: MessageEvent) => {
			if (
				!['https://openwebui.com', 'https://www.openwebui.com', 'http://localhost:9999'].includes(
					event.origin
				)
			) {
				return;
			}

			try {
				let data = JSON.parse(event.data);

				if (data?.info) {
					data = data.info;
				}

				model = data;
			} catch (e) {
				console.error('Failed to parse message data:', e);
			}
		};
		window.addEventListener('message', handleMessageEvent);

		if (window.opener ?? false) {
			window.opener.postMessage('loaded', '*');
		}

		if (sessionStorage.model) {
			model = JSON.parse(sessionStorage.model);
			sessionStorage.removeItem('model');
		}

		return () => {
			window.removeEventListener('message', handleMessageEvent);
		};
	});
</script>

<!-- [Gradient] All workspace assistants use the simple editor, including imported models. -->
{#key model}
	<SimpleModelEditor {model} edit={false} {onSubmit} />
{/key}
