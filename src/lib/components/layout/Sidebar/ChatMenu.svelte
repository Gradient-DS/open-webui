<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, tick } from 'svelte';

	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownSub from '$lib/components/common/DropdownSub.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Pencil from '$lib/components/icons/Pencil.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Tags from '$lib/components/chat/Tags.svelte';
	import Share from '$lib/components/icons/Share.svelte';
	import ArchiveBox from '$lib/components/icons/ArchiveBox.svelte';
	import DocumentDuplicate from '$lib/components/icons/DocumentDuplicate.svelte';
	import Bookmark from '$lib/components/icons/Bookmark.svelte';
	import BookmarkSlash from '$lib/components/icons/BookmarkSlash.svelte';
	import {
		getChatById,
		getChatPinnedStatusById,
		toggleChatPinnedStatusById
	} from '$lib/apis/chats';
	import { chats, config, folders, settings, theme, user } from '$lib/stores';
	import { createMessagesList } from '$lib/utils';
	import { downloadChatAsPDF, exportChatAsPdf, exportChatAsDocx } from '$lib/apis/utils';
	import { copyFormattedChat } from '$lib/utils/copy';
	import Download from '$lib/components/icons/Download.svelte';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import Messages from '$lib/components/chat/Messages.svelte';

	const i18n = getContext('i18n');

	export let shareHandler: Function;
	export let moveChatHandler: Function;

	export let cloneChatHandler: Function;
	export let archiveChatHandler: Function;
	export let renameHandler: Function;
	export let deleteHandler: Function;
	export let onClose: Function;

	export let chatId = '';

	let show = false;
	let pinned = false;

	let chat = null;
	let showFullMessages = false;

	export let onPinChange: () => void = () => {};

	const pinHandler = async () => {
		await toggleChatPinnedStatusById(localStorage.token, chatId);
		onPinChange();
	};

	const checkPinned = async () => {
		pinned = await getChatPinnedStatusById(localStorage.token, chatId);
	};

	const getChatAsText = async (chat) => {
		const history = chat.chat.history;
		const messages = createMessagesList(history, history.currentId);
		const chatText = messages.reduce((a, message, i, arr) => {
			return `${a}### ${message.role.toUpperCase()}\n${message.content}\n\n`;
		}, '');

		return chatText.trim();
	};

	const downloadTxt = async () => {
		const chat = await getChatById(localStorage.token, chatId);
		if (!chat) {
			return;
		}

		const chatText = await getChatAsText(chat);
		let blob = new Blob([chatText], {
			type: 'text/plain'
		});

		saveAs(blob, `chat-${chat.chat.title}.txt`);
	};

	const downloadPdf = async () => {
		chat = await getChatById(localStorage.token, chatId);
		if (!chat) {
			return;
		}

		// Check if stylized (screenshot) PDF is configured server-side
		if ($config?.features?.use_stylized_pdf_export) {
			const [{ default: jsPDF }, { default: html2canvas }] = await Promise.all([
				import('jspdf'),
				import('html2canvas-pro')
			]);

			showFullMessages = true;
			await tick();

			const containerElement = document.getElementById('full-messages-container');
			if (containerElement) {
				try {
					const isDarkMode = document.documentElement.classList.contains('dark');
					const virtualWidth = 800;

					const clonedElement = containerElement.cloneNode(true);
					clonedElement.classList.add('text-black');
					clonedElement.classList.add('dark:text-white');
					clonedElement.style.width = `${virtualWidth}px`;
					clonedElement.style.position = 'absolute';
					clonedElement.style.left = '-9999px';
					clonedElement.style.height = 'auto';
					document.body.appendChild(clonedElement);

					await new Promise((r) => setTimeout(r, 100));

					const canvas = await html2canvas(clonedElement, {
						backgroundColor: isDarkMode ? '#000' : '#fff',
						useCORS: true,
						scale: 2,
						width: virtualWidth
					});

					document.body.removeChild(clonedElement);

					const pdf = new jsPDF('p', 'mm', 'a4');
					const pageWidthMM = 210;
					const pageHeightMM = 297;
					const pxPerPDFMM = canvas.width / pageWidthMM;
					const pagePixelHeight = Math.floor(pxPerPDFMM * pageHeightMM);

					let offsetY = 0;
					let page = 0;

					while (offsetY < canvas.height) {
						const sliceHeight = Math.min(pagePixelHeight, canvas.height - offsetY);
						const pageCanvas = document.createElement('canvas');
						pageCanvas.width = canvas.width;
						pageCanvas.height = sliceHeight;
						const ctx = pageCanvas.getContext('2d');
						ctx.drawImage(canvas, 0, offsetY, canvas.width, sliceHeight, 0, 0, canvas.width, sliceHeight);
						const imgData = pageCanvas.toDataURL('image/jpeg', 0.7);
						const imgHeightMM = (sliceHeight * pageWidthMM) / canvas.width;
						if (page > 0) pdf.addPage();
						if (isDarkMode) {
							pdf.setFillColor(0, 0, 0);
							pdf.rect(0, 0, pageWidthMM, pageHeightMM, 'F');
						}
						pdf.addImage(imgData, 'JPEG', 0, 0, pageWidthMM, imgHeightMM);
						offsetY += sliceHeight;
						page++;
					}

					pdf.save(`chat-${chat.chat.title}.pdf`);
					showFullMessages = false;
				} catch (error) {
					console.error('Error generating PDF', error);
				}
			}
			return;
		}

		// Server-side PDF export
		const history = chat.chat.history;
		const messages = createMessagesList(history, history.currentId);
		const exportMessages = messages.map((m) => ({
			role: m.role,
			content: m.content,
			sources: m.sources || []
		}));

		try {
			const blob = await exportChatAsPdf(localStorage.token, chat.chat.title, exportMessages);
			if (blob) saveAs(blob, `chat-${chat.chat.title}.pdf`);
		} catch (e) {
			console.error('PDF export failed:', e);
			toast.error($i18n.t('Failed to export PDF'));
		}
	};

	const downloadDocx = async () => {
		chat = await getChatById(localStorage.token, chatId);
		if (!chat) {
			return;
		}

		const history = chat.chat.history;
		const messages = createMessagesList(history, history.currentId);
		const exportMessages = messages.map((m) => ({
			role: m.role,
			content: m.content,
			sources: m.sources || []
		}));

		try {
			const blob = await exportChatAsDocx(localStorage.token, chat.chat.title, exportMessages);
			if (blob) saveAs(blob, `chat-${chat.chat.title}.docx`);
		} catch (e) {
			console.error('DOCX export failed:', e);
			toast.error($i18n.t('Failed to export Word document'));
		}
	};

	const downloadJSONExport = async () => {
		const chat = await getChatById(localStorage.token, chatId);

		if (chat) {
			let blob = new Blob([JSON.stringify([chat])], {
				type: 'application/json'
			});
			saveAs(blob, `chat-export-${Date.now()}.json`);
		}
	};

	$: if (show) {
		checkPinned();
	}
</script>

{#if chat && showFullMessages}
	<div class="hidden w-full h-full flex-col">
		<div id="full-messages-container">
			<Messages
				className="h-full flex pt-4 pb-8 w-full"
				chatId={`chat-preview-${chat?.id ?? ''}`}
				user={$user}
				readOnly={true}
				history={chat.chat.history}
				messages={chat.chat.messages}
				autoScroll={true}
				sendMessage={() => {}}
				continueResponse={() => {}}
				regenerateResponse={() => {}}
				messagesCount={null}
				editCodeBlock={false}
			/>
		</div>
	</div>
{/if}

<Dropdown
	bind:show
	onOpenChange={(state) => {
		if (state === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		<slot />
	</Tooltip>

	<div slot="content">
		<div
			class="select-none min-w-[200px] rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg transition"
		>
			{#if $user?.role === 'admin' || ($user.permissions?.chat?.share ?? true)}
				<button
					draggable="false"
					class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
					on:click={() => {
						shareHandler();
					}}
				>
					<Share strokeWidth="1.5" />
					<div class="flex items-center">{$i18n.t('Share')}</div>
				</button>
			{/if}

			<DropdownSub
				contentClass="select-none rounded-2xl p-1 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg border border-gray-100 dark:border-gray-800"
			>
				<button
					slot="trigger"
					draggable="false"
					class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
				>
					<Download strokeWidth="1.5" />
					<div class="flex items-center">{$i18n.t('Download')}</div>
				</button>

				{#if $user?.role === 'admin' || ($user.permissions?.chat?.export ?? true)}
					<button
						draggable="false"
						class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
						on:click={() => {
							downloadJSONExport();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('Export chat (.json)')}</div>
					</button>
				{/if}

				<button
					draggable="false"
					class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
					on:click={() => {
						downloadTxt();
					}}
				>
					<div class="flex items-center line-clamp-1">{$i18n.t('Plain text (.txt)')}</div>
				</button>

				<button
					draggable="false"
					class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl select-none w-full"
					on:click={() => {
						downloadPdf();
					}}
				>
					<div class="flex items-center line-clamp-1">{$i18n.t('PDF document (.pdf)')}</div>
				</button>

				<button
					draggable="false"
					class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl select-none w-full"
					on:click={() => {
						downloadDocx();
					}}
				>
					<div class="flex items-center line-clamp-1">{$i18n.t('Word document (.docx)')}</div>
				</button>
			</DropdownSub>

			<button
				draggable="false"
				class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
				on:click={async () => {
					const chatData = await getChatById(localStorage.token, chatId);
					if (!chatData) return;
					const res = await copyFormattedChat(chatData);
					if (res) {
						toast.success($i18n.t('Copied to clipboard'));
						show = false;
					}
				}}
			>
				<Clipboard className="size-4" strokeWidth="1.5" />
				<div class="flex items-center">{$i18n.t('Copy')}</div>
			</button>

			<button
				draggable="false"
				class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
				on:click={() => {
					renameHandler();
				}}
			>
				<Pencil strokeWidth="1.5" />
				<div class="flex items-center">{$i18n.t('Rename')}</div>
			</button>

			<hr class="border-gray-50/30 dark:border-gray-800/30 my-1" />

			<button
				draggable="false"
				class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
				on:click={() => {
					pinHandler();
				}}
			>
				{#if pinned}
					<BookmarkSlash strokeWidth="1.5" />
					<div class="flex items-center">{$i18n.t('Unpin')}</div>
				{:else}
					<Bookmark strokeWidth="1.5" />
					<div class="flex items-center">{$i18n.t('Pin')}</div>
				{/if}
			</button>

			<button
				draggable="false"
				class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
				on:click={() => {
					show = false;
					cloneChatHandler();
				}}
			>
				<DocumentDuplicate strokeWidth="1.5" />
				<div class="flex items-center">{$i18n.t('Clone')}</div>
			</button>

			{#if chatId && $folders.length > 0}
				<DropdownSub
					contentClass="select-none rounded-2xl p-1 z-50 bg-white dark:bg-gray-850 dark:text-white border border-gray-100 dark:border-gray-800 shadow-lg max-h-52 overflow-y-auto scrollbar-hidden"
				>
					<button
						slot="trigger"
						draggable="false"
						class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl select-none w-full"
					>
						<Folder />
						<div class="flex items-center">{$i18n.t('Move')}</div>
					</button>

					{#each $folders.sort((a, b) => b.updated_at - a.updated_at) as folder}
						<button
							draggable="false"
							class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl overflow-hidden w-full"
							on:click={() => {
								moveChatHandler(chatId, folder.id);
							}}
						>
							<div class="shrink-0">
								<Folder />
							</div>

							<div class="truncate">{folder?.name ?? 'Folder'}</div>
						</button>
					{/each}
				</DropdownSub>
			{/if}

			<button
				draggable="false"
				class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
				on:click={() => {
					archiveChatHandler();
				}}
			>
				<ArchiveBox strokeWidth="1.5" />
				<div class="flex items-center">{$i18n.t('Archive')}</div>
			</button>

			<button
				draggable="false"
				class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
				on:click={() => {
					deleteHandler();
				}}
			>
				<GarbageBin strokeWidth="1.5" />
				<div class="flex items-center">{$i18n.t('Delete')}</div>
			</button>
		</div>
	</div>
</Dropdown>
