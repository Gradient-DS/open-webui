<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, tick } from 'svelte';

	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import DropdownSub from '$lib/components/common/DropdownSub.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Tags from '$lib/components/chat/Tags.svelte';
	import {
		getChatById,
		getChatPinnedStatusById,
		toggleChatPinnedStatusById
	} from '$lib/apis/chats';
	import { chats, config, folders, settings, theme, user } from '$lib/stores';
	import { createMessagesList } from '$lib/utils';
	import { getOutputText } from '$lib/components/chat/Messages/structuredOutput';
	import { downloadChatAsPDF, exportChatAsPdf, exportChatAsDocx } from '$lib/apis/utils';
	import ArchiveBoxIcon from '$lib/components/icons/ArchiveBox.svelte';
	import CopyIcon from './icons/Copy.svelte';
	import DownloadIcon from './icons/Download.svelte';
	import EditPencilIcon from './icons/EditPencil.svelte';
	import FolderIcon from './icons/Folder.svelte';
	// [Gradient] Formatted chat export and copy.
	import { copyFormattedChat } from '$lib/utils/copy';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';
	import Messages from '$lib/components/chat/Messages.svelte';
	import PinIcon from './icons/Pin.svelte';
	import PinSlashIcon from './icons/PinSlash.svelte';
	import ShareIcon from './icons/Share.svelte';
	import TrashIcon from './icons/Trash.svelte';
	import ChatCheckIcon from '$lib/components/icons/ChatCheck.svelte';

	const i18n = getContext('i18n');

	export let shareHandler: Function;
	export let moveChatHandler: Function;

	export let cloneChatHandler: Function;
	export let archiveChatHandler: Function;
	export let renameHandler: Function;
	export let deleteHandler: Function;
	export let onOpen: () => void = () => {};
	export let onClose: Function;
	export let markUnreadHandler: Function = () => {};

	export let chatId = '';

	let dropdown: Dropdown;
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
			const content = getOutputText(message.output) || message.content || '';
			return `${a}### ${message.role.toUpperCase()}\n${content}\n\n`;
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

					// Override content-visibility so html2canvas can capture all messages
					clonedElement.querySelectorAll('.message-listitem').forEach((el) => {
						el.style.contentVisibility = 'visible';
					});

					// Let the browser compute layout for the cloned element
					await new Promise((r) => requestAnimationFrame(r));

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
						ctx.drawImage(
							canvas,
							0,
							offsetY,
							canvas.width,
							sliceHeight,
							0,
							0,
							canvas.width,
							sliceHeight
						);
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
	bind:this={dropdown}
	bind:show
	onOpenChange={(state) => {
		if (state) {
			onOpen();
		} else {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		<slot />
	</Tooltip>

	<div slot="content">
		<DropdownMenu className="select-none min-w-[12.5rem] transition">
			{#if $user?.role === 'admin' || ($user.permissions?.chat?.share ?? true)}
				<button
					draggable="false"
					class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 w-full"
					on:click={() => {
						shareHandler();
					}}
				>
					<ShareIcon className="size-3.5" strokeWidth="1.5" />
					<div class="flex items-center">{$i18n.t('Share')}</div>
				</button>
			{/if}

			{#if $user?.role === 'admin' || ($user.permissions?.chat?.export ?? true)}
				<DropdownSub contentClass="select-none z-50">
					<button
						slot="trigger"
						draggable="false"
						class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 w-full"
					>
						<DownloadIcon className="size-3.5" strokeWidth="1.5" />
						<div class="flex items-center">{$i18n.t('Download')}</div>
					</button>

					<button
						draggable="false"
						class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 w-full"
						on:click={() => {
							downloadJSONExport();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('Export chat (.json)')}</div>
					</button>

					<button
						draggable="false"
						class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 w-full"
						on:click={() => {
							downloadTxt();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('Plain text (.txt)')}</div>
					</button>

					<button
						draggable="false"
						class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 select-none w-full"
						on:click={() => {
							downloadPdf();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('PDF document (.pdf)')}</div>
					</button>

					{#if $config?.features?.enable_docx_export ?? true}
						<button
							draggable="false"
							class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl select-none w-full"
							on:click={() => {
								downloadDocx();
							}}
						>
							<div class="flex items-center line-clamp-1">{$i18n.t('Word document (.docx)')}</div>
						</button>
					{/if}
				</DropdownSub>
			{/if}

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
					dropdown.close();
					renameHandler();
				}}
			>
				<EditPencilIcon className="size-3.5" strokeWidth="1.5" />
				<div class="flex items-center">{$i18n.t('Rename')}</div>
			</button>

			<button
				draggable="false"
				class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 w-full"
				on:click={() => {
					dropdown.close();
					markUnreadHandler();
				}}
			>
				<ChatCheckIcon className="size-3.5" strokeWidth="1.5" />
				<div class="flex items-center">{$i18n.t('Mark as unread')}</div>
			</button>

			<hr class="border-gray-50/30 dark:border-gray-800/30 mx-1 my-0.5" />

			<button
				draggable="false"
				class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 w-full"
				on:click={() => {
					dropdown.close();
					pinHandler();
				}}
			>
				{#if pinned}
					<PinSlashIcon className="size-3.5" strokeWidth="1.5" />
					<div class="flex items-center">{$i18n.t('Unpin')}</div>
				{:else}
					<PinIcon className="size-3.5" strokeWidth="1.5" />
					<div class="flex items-center">{$i18n.t('Pin')}</div>
				{/if}
			</button>

			{#if $user?.role === 'admin' || ($user?.permissions?.chat?.import ?? true)}
				<button
					draggable="false"
					class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 w-full"
					on:click={() => {
						dropdown.close();
						cloneChatHandler();
					}}
				>
					<CopyIcon className="size-3.5" strokeWidth="1.5" />
					<div class="flex items-center">{$i18n.t('Clone')}</div>
				</button>
			{/if}

			{#if chatId && $folders.length > 0}
				<DropdownSub contentClass="select-none z-50 max-h-52 overflow-y-auto scrollbar-hidden">
					<button
						slot="trigger"
						draggable="false"
						class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 select-none w-full"
					>
						<FolderIcon className="size-3.5" />
						<div class="flex items-center">{$i18n.t('Move')}</div>
					</button>

					{#each $folders.sort((a, b) => b.updated_at - a.updated_at) as folder}
						<button
							draggable="false"
							class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 overflow-hidden w-full"
							on:click={() => {
								moveChatHandler(chatId, folder.id);
							}}
						>
							<div class="shrink-0">
								<FolderIcon className="size-3.5" />
							</div>

							<div class="truncate">{folder?.name ?? 'Folder'}</div>
						</button>
					{/each}
				</DropdownSub>
			{/if}

			<button
				draggable="false"
				class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 w-full"
				on:click={() => {
					archiveChatHandler();
				}}
			>
				<ArchiveBoxIcon className="size-3.5" strokeWidth="1.7" />
				<div class="flex items-center">{$i18n.t('Archive')}</div>
			</button>

			{#if $user?.role === 'admin' || ($user?.permissions?.chat?.delete ?? true)}
				<button
					draggable="false"
					class="flex h-[1.6875rem] gap-2 items-center rounded-xl px-2 text-[0.8125rem] cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900 w-full"
					on:click={() => {
						deleteHandler();
					}}
				>
					<TrashIcon className="size-3.5" strokeWidth="1.5" />
					<div class="flex items-center">{$i18n.t('Delete')}</div>
				</button>
			{/if}
		</DropdownMenu>
	</div>
</Dropdown>
