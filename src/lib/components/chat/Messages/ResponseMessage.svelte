<script lang="ts">
	// [Gradient] Message identity is durable and independent of the current chat selection.
	import { effectiveModel } from '$lib/utils/assistants';
	import EditPencilIcon from '$lib/components/layout/Sidebar/icons/EditPencil.svelte';
	import CopyIcon from '$lib/components/layout/Sidebar/icons/Copy.svelte';
	import TrashIcon from '$lib/components/layout/Sidebar/icons/Trash.svelte';
	import ChevronRightIcon from '$lib/components/layout/Sidebar/icons/ChevronRight.svelte';
	import { resolveLocalizedFunction } from '$lib/utils/localizedContent';
	import { functions as localizedFunctions } from '$lib/stores';
	import { toast } from 'svelte-sonner';

	import { createEventDispatcher, onDestroy } from 'svelte';
	import { onMount, tick, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType, t } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	const dispatch = createEventDispatcher();

	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { createNewFeedback, getFeedbackById, updateFeedbackById } from '$lib/apis/evaluations';
	import { getChatById } from '$lib/apis/chats';
	import { generateTags } from '$lib/apis';
	import { exportChatAsPdf, exportChatAsDocx } from '$lib/apis/utils';

	import {
		audioQueue,
		config,
		models,
		settings,
		temporaryChatEnabled,
		TTSWorker,
		user
	} from '$lib/stores';
	import { synthesizeOpenAISpeech } from '$lib/apis/audio';
	import { imageGenerations } from '$lib/apis/images';
	import {
		copyToClipboard as _copyToClipboard,
		approximateToHumanReadable,
		getMessageContentParts,
		sanitizeResponseContent,
		createMessagesList,
		formatMessageTimestamp,
		formatMessageTimestampFull,
		removeDetails,
		removeAllDetails
	} from '$lib/utils';
	import { canContinueResponse, isFeatureEnabled } from '$lib/utils/features';
	import { answeredByLabel } from '$lib/utils/models/catalog';
	import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
	import { resolveLocalizedModelName } from '$lib/utils/localizedContent';
	import equal from 'fast-deep-equal';

	import Name from './Name.svelte';
	import ProfileImage from './ProfileImage.svelte';
	import Skeleton from './Skeleton.svelte';
	import Image from '$lib/components/common/Image.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import RateComment from './RateComment.svelte';
	import WebSearchResults from './ResponseMessage/WebSearchResults.svelte';
	import Sparkles from '$lib/components/layout/Sidebar/icons/Sparkles.svelte';

	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';

	import Error from './Error.svelte';
	import Citations from './SoevCitations.svelte'; // [Gradient]
	import { answerText } from './Citations/citedSources'; // [Gradient]
	import CodeExecutions from './CodeExecutions.svelte';
	import ContentRenderer from './ContentRenderer.svelte';
	import PresentUIDispatcher from './Markdown/PresentUIDispatcher.svelte';
	import { KokoroWorker } from '$lib/workers/KokoroWorker';
	import { consentLabels } from '$lib/utils/live-connections';
	import ConnectOneDrive from './ConnectOneDrive.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import FollowUps from './ResponseMessage/FollowUps.svelte';
	import { fade } from 'svelte/transition';
	import { flyAndScale } from '$lib/utils/transitions';
	import RegenerateMenu from './ResponseMessage/RegenerateMenu.svelte';
	import StatusHistory from './ResponseMessage/StatusHistory.svelte';
	import ReasoningBullet from './ResponseMessage/StatusHistory/ReasoningBullet.svelte';
	import SubAgentGroup from './SubAgents/SubAgentGroup.svelte';
	import { reduceSubAgents } from './SubAgents/reduceSubAgents';
	import type { SubAgentEvent, SubAgentGroupVM } from '$lib/types/subagent';
	import {
		detectMergeProtocol,
		mergeStatusAndReasoning,
		buildResponseBlocks,
		type ContentBlock,
		parseToolOffsets
	} from './ResponseMessage/mergeHistory';
	import FullHeightIframe from '$lib/components/common/FullHeightIframe.svelte';
	import TurnNotices from './ResponseMessage/TurnNotices.svelte';
	import OutputEditView from './OutputEditView.svelte';
	import {
		getOutputProseRuns,
		getOutputStreamAnchors,
		replaceOutputMessageText,
		type OutputItem
	} from './structuredOutput';

	interface MessageType {
		action_required?: { kind: string; provider: string };
		user_id?: string;
		id: string;
		model: string;
		assistant_id?: string; // [Gradient]
		meta?: { answered_model?: string }; // [Gradient] Set when a fallback answered a v2 turn.
		notices?: string[]; // [Claude] Chat items a v2 turn ran without, shown above its answer.
		content: string;
		output?: OutputItem[];
		files?: {
			type: string;
			url: string;
			id?: string;
			attached_by?: string;
			name?: string;
			content_type?: string;
			size?: number;
		}[];
		timestamp: number;
		role: string;
		statusHistory?: {
			done: boolean;
			action: string;
			description: string;
			urls?: string[];
			query?: string;
		}[];
		status?: {
			done: boolean;
			action: string;
			description: string;
			urls?: string[];
			query?: string;
		};
		done: boolean;
		error?: boolean | { content: string };
		sources?: string[];
		uiBlocks?: { id: string; name: string; props: any }[];
		code_executions?: {
			uuid: string;
			name: string;
			code: string;
			language?: string;
			result?: {
				error?: string;
				output?: string;
				files?: { name: string; url: string }[];
			};
		}[];
		info?: {
			openai?: boolean;
			prompt_tokens?: number;
			completion_tokens?: number;
			total_tokens?: number;
			eval_count?: number;
			eval_duration?: number;
			prompt_eval_count?: number;
			prompt_eval_duration?: number;
			total_duration?: number;
			load_duration?: number;
			usage?: unknown;
		};
		annotation?: { type: string; rating: number };
	}

	export let chatId = '';
	export let history;
	export let messageId;
	export let selectedModels = [];

	let messageSource = history.messages[messageId];
	let message: MessageType = structuredClone(messageSource);
	$: if (history.messages) {
		const source = history.messages[messageId];
		if (source) {
			// Fast path for the fields that change most often while streaming.
			// Responses streams update output even when legacy content is unchanged.
			if (source !== messageSource) {
				messageSource = source;
				message = structuredClone(source);
			} else if (
				message.content !== source.content ||
				message.done !== source.done ||
				message.output?.length !== source.output?.length
			) {
				message = structuredClone(source);
			} else if (!equal(message, source)) {
				// Slow path: full comparison for infrequent changes (sources, annotations, status, etc.)
				message = structuredClone(source);
			}
		}
	}

	export let siblings;

	export let setInputText: Function = () => {};
	export let gotoMessage: Function = () => {};
	export let showPreviousMessage: Function;
	export let showNextMessage: Function;

	export let updateChat: Function;
	export let editMessage: Function;
	export let saveMessage: Function;
	export let rateMessage: Function;
	export let actionMessage: Function;
	export let deleteMessage: Function;

	export let submitMessage: Function;
	export let continueResponse: Function;
	export let regenerateResponse: Function;
	export let forkHandler: Function | null = null;

	export let addMessages: Function;
	export let onToolCallResolved: Function = () => {};

	export let isLastMessage = true;
	export let readOnly = false;
	export let shareMode: 'continue' | null = null;
	export let allowDelete = true;
	export let compactPreview = false;
	// [Gradient] Answers are read-only: an agent's thread keeps what it answered, so an edit here would diverge
	// from what the model knows. No edit button, and code blocks show without their editor.
	const ANSWERS_EDITABLE = false;
	export let editCodeBlock = ANSWERS_EDITABLE;
	export let topPadding = false;
	export let onInsertToNote: ((content: string) => void) | null = null;

	let citationsElement: HTMLDivElement;

	let contentContainerElement: HTMLDivElement;
	let buttonsContainerElement: HTMLDivElement;
	let showDeleteConfirm = false;

	let model = null;
	// [Gradient] Legacy messages still resolve their assistant directly via message.model.
	$: assistant = $models.find((m) => m.id === message.assistant_id);
	$: model = effectiveModel(
		$models.find((m) => m.id === message.model),
		assistant
	);
	$: headerModel = assistant ?? model;
	$: localizedModelName = headerModel
		? resolveLocalizedModelName(headerModel, $i18n.language)
		: message.model;
	// [Gradient] soev-api reports the model that answered when a fallback replaced the requested one.
	$: answeredBy = answeredByLabel(message?.meta?.answered_model, message.model, $models);

	$: statusEntries = (() => {
		const raw = message?.statusHistory ?? [...(message?.status ? [message?.status] : [])];
		// The agent emits one "summary" StatusUpdate after each tool completes
		// (so the dropdown header settles to "N tools aangeroepen in M seconden"
		// while the answer streams, not after) plus a final post-loop summary
		// with the total turn time. Keep only the most recent one — earlier
		// emissions are superseded by the latest count + time.
		let lastSummaryIdx = -1;
		for (let i = raw.length - 1; i >= 0; i--) {
			if (raw[i]?.action === 'summary') {
				lastSummaryIdx = i;
				break;
			}
		}
		return raw.filter((s, i) => s?.action !== 'summary' || i === lastSummaryIdx);
	})();

	// Parse <details type="reasoning"> blocks out of message.content. Each match
	// becomes a reasoning bullet item in the merged StatusHistory list. We
	// extract the original block attributes too (done, duration) so the
	// ReasoningBullet can reuse Collapsible's i18n-aware "Thought for N
	// seconds" label rendering.
	function parseAttributes(openTag: string): Record<string, string> {
		const attrs: Record<string, string> = {};
		const attrRe = /(\w+)="([^"]*)"/g;
		let am;
		while ((am = attrRe.exec(openTag)) !== null) {
			attrs[am[1]] = am[2];
		}
		return attrs;
	}

	type ReasoningItem = {
		kind: 'reasoning';
		summary: string;
		body: string;
		attributes: Record<string, string>;
		contentOffset: number;
	};

	function parseReasoningItems(content: string): ReasoningItem[] {
		if (!content.includes('<details type="reasoning"')) return [];
		const items: ReasoningItem[] = [];
		const re = /<details type="reasoning"[^>]*>([\s\S]*?)<\/details>/g;
		let match;
		while ((match = re.exec(content)) !== null) {
			const fullBlock = match[0];
			const openTagMatch = fullBlock.match(/^<details[^>]*>/);
			const attributes = openTagMatch ? parseAttributes(openTagMatch[0]) : {};
			const summaryMatch = fullBlock.match(/<summary>([\s\S]*?)<\/summary>/);
			const body = match[1].replace(/<summary>[\s\S]*?<\/summary>/, '').trim();
			items.push({
				kind: 'reasoning',
				summary: summaryMatch?.[1]?.trim() ?? '',
				body,
				attributes,
				contentOffset: match.index
			});
		}
		return items;
	}

	// Memoize content-derived parses on the content string identity so that
	// reactive cascades from unrelated message-field updates (e.g. each
	// subagent SSE event triggers a message re-prop) don't re-run the
	// O(content-length) regex scans and don't churn a new array reference on
	// every fire. Without this, dependents like ``responseParts`` see
	// reasoningItems as "changed" on every dispatch even when content is
	// byte-identical — which propagates into Svelte each-block rekeys and is
	// a primary source of the end-of-stream flicker.
	let _memoContent: string | undefined;
	let _memoOutput: OutputItem[] | undefined;
	let _memoReasoningItems: ReasoningItem[] = [];
	let _memoToolOffsets: number[] = [];

	function memoContentParses(content: string, output: OutputItem[] | undefined): void {
		if (content === _memoContent && output === _memoOutput) return;
		_memoContent = content;
		_memoOutput = output;
		// [Gradient] Upstream v0.10.2 streams/persists ``message.output``
		// items and leaves ``message.content`` empty, so the reasoning/tool
		// anchors are derived from output when present. Content parsing
		// remains as the fallback for messages persisted before the output
		// pipeline existed.
		const next = output?.length
			? getOutputStreamAnchors(output)
			: { reasoningItems: parseReasoningItems(content), toolOffsets: parseToolOffsets(content) };
		// Keep array identity stable when the derived anchors are unchanged —
		// ``message.output`` is a fresh structuredClone on every prop sync, so
		// a reference-keyed memo alone would churn dependents on every event.
		if (!equal(next.reasoningItems, _memoReasoningItems)) {
			_memoReasoningItems = next.reasoningItems;
		}
		if (!equal(next.toolOffsets, _memoToolOffsets)) {
			_memoToolOffsets = next.toolOffsets;
		}
	}

	$: memoContentParses(message?.content ?? '', message?.output);
	$: reasoningItems = _memoReasoningItems;
	$: toolOffsets = _memoToolOffsets;

	// Merge reasoning items into statusEntries in stream order. The merge
	// strategy depends on which protocol produced the wire data; see
	// ``mergeHistory.ts`` for the protocol matrix. ``protocol`` is computed
	// once and is the single source of truth for "how do we render this
	// turn" — the dispatcher inside ``mergeStatusAndReasoning`` reads it,
	// and Phase 3 will lift the standalone-reasoning mount onto it too.
	$: protocol = detectMergeProtocol(statusEntries, reasoningItems, toolOffsets);
	$: mergedHistory = mergeStatusAndReasoning(statusEntries, reasoningItems, toolOffsets);

	// [Gradient] Cut the turn into alternating tool-group / prose blocks so the
	// model's inter-tool commentary renders where it was streamed rather than
	// collecting at the bottom of the message. The prose and the ``<details>``
	// anchors share one offset axis, which is what makes the ordering exact.
	// Only ``positional`` has markers to order against; the other protocols
	// keep the previous single-dropdown-then-content layout untouched.
	// The prose source must share ``toolOffsets``'s axis: with ``message.output``
	// present the anchors are ORDINAL (``getOutputStreamAnchors``), so the prose
	// comes from ``getOutputProseRuns`` on that same axis; without output the
	// anchors are character offsets into ``message.content``. Cutting content by
	// character offset against ordinal anchors is what collapsed the commentary
	// to the bottom on output-pipeline deployments.
	$: responseBlocks =
		protocol === 'positional'
			? buildResponseBlocks(
					mergedHistory,
					message?.output?.length ? getOutputProseRuns(message.output) : (message?.content ?? ''),
					toolOffsets
				)
			: [];
	// A lone block is just an ordinary answer — nothing to interleave.
	$: useBlockLayout = protocol === 'positional' && responseBlocks.length > 1;
	// The tail is the final answer. It keeps the full ContentRenderer (floating
	// buttons, save/preview, source modal) and is the ONLY place the answer
	// renders, so no text is ever shown twice. A turn that ends on a tool call
	// has no tail.
	$: tailBlock =
		useBlockLayout && responseBlocks.at(-1)?.kind === 'content'
			? (responseBlocks.at(-1) as ContentBlock)
			: null;
	$: leadingBlocks = useBlockLayout
		? tailBlock
			? responseBlocks.slice(0, -1)
			: responseBlocks
		: [];
	$: renderedContent = useBlockLayout ? (tailBlock?.text ?? '') : (message?.content ?? '');

	// [Gradient] ONE activity timeline per turn. buildResponseBlocks already orders
	// tool groups and the model's inter-tool prose on a single offset axis; flatten
	// that into one row list rather than mounting a StatusHistory per status-group.
	// Per-group mounting gave each group its own header, its own expand state and
	// its own header promotion, which is why a multi-tool turn read as several
	// separate boxes instead of one sequence.
	$: timelineItems = useBlockLayout
		? leadingBlocks.flatMap((b) => (b.kind === 'status-group' ? b.items : [b]))
		: mergedHistory;

	// Whether at least one tool actually ran this turn. Used together with the
	// streaming heuristic below to gate the StatusHistory dropdown:
	//   - During streaming (message.done === false): show as soon as there is
	//     ANY reasoning or status content, so users see live progress.
	//   - After completion (message.done === true): only show if a tool fired —
	//     reasoning-only turns drop the dropdown per Phase D.
	$: hasToolCalls = statusEntries.some((s) => s?.action && s.action !== 'reasoning_step');
	// Only status entries gate the StatusHistory dropdown during streaming.
	// Reasoning-only turns are handled by the standalone ReasoningBullet
	// path further below — counting reasoning here would cause both paths to
	// render at once (double pill), and would force a visual jump from
	// standalone to dropdown the moment the first status event arrives in a
	// tool turn.
	$: streamingHasActivity = !(message?.done ?? false) && statusEntries.length > 0;
	$: shouldShowStatusHistory =
		(model?.info?.meta?.capabilities?.status_updates ?? true) &&
		(((hasToolCalls || streamingHasActivity) && mergedHistory.length > 0) ||
			(statusEntries.length > 0 && !(statusEntries.at(-1)?.hidden ?? false)));

	$: hasVisibleStatus = shouldShowStatusHistory;

	// Build a time-ordered list of reasoning items and subagent groups for the
	// reasoning_only protocol path (bezwaar turns). Each subagent-group part
	// carries the already-reduced ``SubAgentGroupVM`` — SubAgentGroup renders
	// it directly, no second pass over the raw events. List is sorted by
	// ``started_at`` (unix-ms) ascending.
	//
	// This reactive declaration is only consumed by the reasoning_only render
	// branch below; non-bezwaar turns (shouldShowStatusHistory path) are
	// unaffected.
	type ResponsePart =
		| { kind: 'reasoning'; ts: number; item: ReasoningItem }
		| { kind: 'subagent-group'; ts: number; group_id: string; group: SubAgentGroupVM };

	const _EMPTY_SUBAGENT_EVENTS: SubAgentEvent[] = [];

	function buildResponseParts(
		items: ReasoningItem[],
		subagentEvents: SubAgentEvent[]
	): ResponsePart[] {
		const groups = reduceSubAgents(subagentEvents);
		const parts: ResponsePart[] = [];

		for (const item of items) {
			const ts = parseInt(item.attributes?.started_at ?? '0', 10);
			parts.push({ kind: 'reasoning', ts, item });
		}

		for (const g of groups) {
			// SubagentStartEvent.started_at is epoch SECONDS (per
			// agents/core/types.py); reasoning items' started_at attribute is
			// epoch MILLISECONDS (middleware.py renders it as ms). Normalize
			// the subagent side to ms so both axes are comparable.
			const ts = Math.min(...g.cards.map((c) => c.started_at)) * 1000;
			parts.push({ kind: 'subagent-group', ts, group_id: g.parallel_group_id, group: g });
		}

		parts.sort((a, b) => a.ts - b.ts);
		return parts;
	}

	// Throttling strategy for the responseParts rebuild:
	//
	// During streaming Chat.svelte pushes onto ``message.subagents`` on every
	// SSE token (200+/sec on Nemotron CoT). Without throttling, every push
	// triggers the full ``buildResponseParts`` → ``reduceSubAgents(allEvents)``
	// pipeline, then SubAgentGroup re-renders, then SubAgentCard fires its
	// scroll effect — O(N²·K) per turn and visibly janky.
	//
	// We collapse multiple pushes within a single animation frame into one
	// rebuild via ``requestAnimationFrame``: each ``$:`` trigger updates the
	// "latest args" snapshot; the first trigger schedules an rAF callback
	// that runs ``buildResponseParts`` against the most recent snapshot when
	// it fires. End result: at most 60Hz UI updates regardless of token rate,
	// while the final state always lands within one frame of the last event.
	//
	// The internal length-based fast-path stays as a second layer of defense:
	// it skips the rebuild when the rAF fires but nothing meaningful changed
	// (e.g. reasoningItems references stayed identical and no new subagent
	// events arrived since the previous frame).
	let _memoRpItemsRef: ReasoningItem[] | undefined;
	let _memoRpEventsRef: SubAgentEvent[] | undefined;
	let _memoRpEventsLen = -1;
	let _memoResponseParts: ResponsePart[] = [];

	let _rpRafId: number | null = null;
	let _rpLatestItems: ReasoningItem[] = [];
	let _rpLatestEvents: SubAgentEvent[] = _EMPTY_SUBAGENT_EVENTS;

	function scheduleResponsePartsRebuild(items: ReasoningItem[], events: SubAgentEvent[]): void {
		_rpLatestItems = items;
		_rpLatestEvents = events;
		// [Gradient] The first part builds at once: the skeleton hides as soon as
		// content arrives, so waiting a frame leaves the message empty for that
		// frame and the first thought pops in after a collapse.
		if (_memoResponseParts.length === 0 && (items.length > 0 || events.length > 0)) {
			if (_rpRafId !== null) {
				cancelAnimationFrame(_rpRafId);
				_rpRafId = null;
			}
			_memoRpItemsRef = items;
			_memoRpEventsRef = events;
			_memoRpEventsLen = events.length;
			_memoResponseParts = buildResponseParts(items, events);
			return;
		}
		if (_rpRafId !== null) return;
		_rpRafId = requestAnimationFrame(() => {
			_rpRafId = null;
			const latestItems = _rpLatestItems;
			const latestEvents = _rpLatestEvents;
			if (
				latestItems === _memoRpItemsRef &&
				latestEvents === _memoRpEventsRef &&
				latestEvents.length === _memoRpEventsLen
			) {
				return;
			}
			_memoRpItemsRef = latestItems;
			_memoRpEventsRef = latestEvents;
			_memoRpEventsLen = latestEvents.length;
			_memoResponseParts = buildResponseParts(latestItems, latestEvents);
		});
	}

	onDestroy(() => {
		if (_rpRafId !== null) {
			cancelAnimationFrame(_rpRafId);
			_rpRafId = null;
		}
	});

	$: scheduleResponsePartsRebuild(reasoningItems, message?.subagents ?? _EMPTY_SUBAGENT_EVENTS);
	$: responseParts = _memoResponseParts;

	// [Gradient] Shared with the Sources panel, which lists the sources this text cites.
	$: visibleResponseContent = answerText(message);
	$: hasResponseContent = Boolean((message.content ?? '').trim() || message.output?.length);
	$: hasAnswerText = Boolean(removeAllDetails(renderedContent ?? '').trim());

	let edit = false;
	let editedContent = '';
	let editedOutput: any[] | null = null;
	let editTextAreaElement: HTMLTextAreaElement;

	let messageIndexEdit = false;

	let speaking = false;
	let speakingIdx: number | undefined;

	let loadingSpeech = false;
	let speakAbort: AbortController | null = null;

	let showRateComment = false;

	const copyToClipboard = async (text, sources: any[] = []) => {
		text = removeAllDetails(text);

		if (($config?.ui?.response_watermark ?? '').trim() !== '') {
			text = `${text}\n\n${$config?.ui?.response_watermark}`;
		}

		const res = await _copyToClipboard(text, null, $settings?.copyFormatted ?? false, sources);
		if (res) {
			toast.success($i18n.t('Copying to clipboard was successful!'));
		}
	};

	let showDownloadMenu = false;

	const getMessageFilename = (ext: string) => {
		const shortId = (message?.id ?? '').slice(0, 8) || 'response';
		return `message-${shortId}.${ext}`;
	};

	const getMessageBodyText = () => {
		let text = removeAllDetails(message.content ?? '');
		if (($config?.ui?.response_watermark ?? '').trim() !== '') {
			text = `${text}\n\n${$config?.ui?.response_watermark}`;
		}
		return text;
	};

	const downloadMessageAsText = () => {
		const blob = new Blob([getMessageBodyText()], { type: 'text/plain;charset=utf-8' });
		saveAs(blob, getMessageFilename('txt'));
	};

	const downloadMessageAsMarkdown = () => {
		const blob = new Blob([getMessageBodyText()], { type: 'text/markdown;charset=utf-8' });
		saveAs(blob, getMessageFilename('md'));
	};

	const buildExportMessagePayload = () => [
		{
			role: message.role,
			content: message.content ?? '',
			sources: message.sources ?? []
		}
	];

	const downloadMessageAsPdf = async () => {
		try {
			const blob = await exportChatAsPdf(
				localStorage.token,
				getMessageFilename('pdf').replace(/\.pdf$/, ''),
				buildExportMessagePayload(),
				{ includeChrome: false }
			);
			if (blob) saveAs(blob, getMessageFilename('pdf'));
		} catch (e) {
			console.error('Message PDF export failed:', e);
			toast.error($i18n.t('Failed to export PDF'));
		}
	};

	const downloadMessageAsDocx = async () => {
		try {
			const blob = await exportChatAsDocx(
				localStorage.token,
				getMessageFilename('docx').replace(/\.docx$/, ''),
				buildExportMessagePayload(),
				{ includeChrome: false }
			);
			if (blob) saveAs(blob, getMessageFilename('docx'));
		} catch (e) {
			console.error('Message DOCX export failed:', e);
			toast.error($i18n.t('Failed to export Word document'));
		}
	};

	const stopAudio = () => {
		speakAbort?.abort();
		speakAbort = null;

		try {
			speechSynthesis.cancel();
			$audioQueue?.stop();
		} catch {}

		speaking = false;
		speakingIdx = undefined;
		loadingSpeech = false;
	};

	// Resolve voice: model-specific > user settings > config default
	const getVoiceId = () =>
		model?.info?.meta?.tts?.voice ??
		($settings?.audio?.tts?.defaultVoice === $config.audio.tts.voice
			? ($settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice)
			: $config?.audio?.tts?.voice);

	const speak = async () => {
		const content = removeAllDetails(visibleResponseContent);
		if (!content.trim().length) {
			toast.info($i18n.t('No content to speak'));
			return;
		}

		stopAudio();
		speakAbort = new AbortController();
		const { signal } = speakAbort;

		speaking = true;

		if ($config.audio.tts.engine === '') {
			let voices = [];
			const getVoicesLoop = setInterval(() => {
				voices = speechSynthesis.getVoices();
				if (voices.length > 0) {
					clearInterval(getVoicesLoop);

					const voice = voices.find((v) => v.voiceURI === getVoiceId());
					const speech = new SpeechSynthesisUtterance(content);
					speech.rate = $settings.audio?.tts?.playbackRate ?? 1;

					speech.onend = () => {
						speaking = false;
						if ($settings.conversationMode) {
							document.getElementById('voice-input-button')?.click();
						}
					};

					if (voice) {
						speech.voice = voice;
					}

					speechSynthesis.speak(speech);
				}
			}, 100);
		} else {
			$audioQueue.setId(`${message.id}`);
			$audioQueue.setPlaybackRate($settings.audio?.tts?.playbackRate ?? 1);
			let synthesizing = true;
			$audioQueue.onStopped = ({ event }) => {
				if (event === 'empty-queue' && synthesizing) return;

				speaking = false;
				speakingIdx = undefined;

				if (event === 'error') {
					speakAbort?.abort();
					toast.error($i18n.t('Audio playback failed'));
				}
			};

			loadingSpeech = true;
			const messageContentParts: string[] = getMessageContentParts(
				content,
				$config?.audio?.tts?.split_on ?? 'punctuation'
			);

			if (!messageContentParts.length) {
				toast.info($i18n.t('No content to speak'));
				speaking = false;
				loadingSpeech = false;
				return;
			}

			const voiceId = getVoiceId();
			console.debug('Prepared message content for TTS', messageContentParts, 'voice:', voiceId);

			if ($settings.audio?.tts?.engine === 'browser-kokoro') {
				if (!$TTSWorker) {
					await TTSWorker.set(
						new KokoroWorker({
							dtype: $settings.audio?.tts?.engineConfig?.dtype ?? 'fp32'
						})
					);

					await $TTSWorker.init();
				}

				for (const [, sentence] of messageContentParts.entries()) {
					if (signal.aborted) return;

					const url = await $TTSWorker
						.generate({ text: sentence, voice: voiceId })
						.catch((error) => {
							console.error(error);
							toast.error(`${error}`);
							speaking = false;
							loadingSpeech = false;
						});

					if (signal.aborted) return;

					if (url && speaking) {
						$audioQueue.enqueue(url);
						loadingSpeech = false;
					}
				}
			} else {
				for (const [, sentence] of messageContentParts.entries()) {
					if (signal.aborted) return;

					const res = await synthesizeOpenAISpeech(localStorage.token, voiceId, sentence).catch(
						(error) => {
							console.error(error);
							toast.error(`${error}`);
							speaking = false;
							loadingSpeech = false;
						}
					);

					if (signal.aborted) return;

					if (res && speaking) {
						const blob = await res.blob();
						const url = URL.createObjectURL(blob);
						$audioQueue.enqueue(url);
						loadingSpeech = false;
					}
				}
			}

			synthesizing = false;
		}
	};

	let preprocessedDetailsCache = [];

	function preprocessForEditing(content: string): string {
		// Replace <details>...</details> with unique ID placeholder
		const detailsBlocks = [];
		let i = 0;

		content = content.replace(/<details[\s\S]*?<\/details>/gi, (match) => {
			detailsBlocks.push(match);
			return `<details id="__DETAIL_${i++}__"/>`;
		});

		// Store original blocks in the editedContent or globally (see merging later)
		preprocessedDetailsCache = detailsBlocks;

		return content;
	}

	function postprocessAfterEditing(content: string): string {
		const restoredContent = content.replace(
			/<details id="__DETAIL_(\d+)__"\/>/g,
			(_, index) => preprocessedDetailsCache[parseInt(index)] || ''
		);

		return restoredContent;
	}

	const editMessageHandler = async () => {
		edit = true;

		if (message.output?.length) {
			// Structured edit: use the block editor
			editedOutput = structuredClone(message.output);
		} else {
			// Legacy text edit: use the textarea
			editedContent = preprocessForEditing(message.content);
		}

		await tick();

		if (!editedOutput && editTextAreaElement) {
			const messagesContainer = document.getElementById('messages-container');
			const savedScrollTop = messagesContainer?.scrollTop ?? 0;

			editTextAreaElement.style.height = '';
			editTextAreaElement.style.height = `${editTextAreaElement.scrollHeight}px`;

			if (messagesContainer) messagesContainer.scrollTop = savedScrollTop;
			editTextAreaElement?.focus({ preventScroll: true });
		}
	};

	const editMessageConfirmHandler = async () => {
		if (editedOutput) {
			editMessage(message.id, { output: editedOutput }, false);
		} else {
			// Legacy text edit
			const messageContent = postprocessAfterEditing(editedContent ?? '');
			editMessage(message.id, { content: messageContent }, false);
		}

		edit = false;
		editedContent = '';
		editedOutput = null;

		await tick();
	};

	const saveAsCopyHandler = async () => {
		if (editedOutput) {
			editMessage(message.id, { output: editedOutput });
		} else {
			const messageContent = postprocessAfterEditing(editedContent ?? '');
			editMessage(message.id, { content: messageContent });
		}

		edit = false;
		editedContent = '';
		editedOutput = null;

		await tick();
	};

	const cancelEditMessage = async () => {
		edit = false;
		editedContent = '';
		editedOutput = null;
		await tick();
	};

	let feedbackLoading = false;

	const feedbackHandler = async (rating: number | null = null, details: object | null = null) => {
		feedbackLoading = true;
		console.log('Feedback', rating, details);

		const updatedMessage = {
			...message,
			annotation: {
				...(message?.annotation ?? {}),
				...(rating !== null ? { rating: rating } : {}),
				...(rating !== null && rating !== message?.annotation?.rating
					? { reason: null, details: null }
					: {}),
				...(details ? details : {})
			}
		};

		const chat = await getChatById(localStorage.token, chatId).catch((error) => {
			toast.error(`${error}`);
		});
		if (!chat) {
			return;
		}

		const messages = createMessagesList(history, message.id);

		let feedbackItem = {
			type: 'rating',
			data: {
				...(updatedMessage?.annotation ? updatedMessage.annotation : {}),
				model_id: message?.selectedModelId ?? message.model,
				...(history.messages[message.parentId].childrenIds.length > 1
					? {
							sibling_model_ids: history.messages[message.parentId].childrenIds
								.filter((id) => id !== message.id)
								.map((id) => history.messages[id]?.selectedModelId ?? history.messages[id].model)
						}
					: {})
			},
			meta: {
				arena: message ? message.arena : false,
				model_id: message.model,
				assistant_id: message.assistant_id, // [Gradient]
				message_id: message.id,
				message_index: messages.length,
				chat_id: chatId
			},
			snapshot: {
				chat: chat
			}
		};

		const baseModels = [
			feedbackItem.data.model_id,
			...(feedbackItem.data.sibling_model_ids ?? [])
		].reduce((acc, modelId) => {
			const model = $models.find((m) => m.id === modelId);
			if (model) {
				acc[model.id] = model?.info?.base_model_id ?? null;
			} else {
				// Log or handle cases where corresponding model is not found
				console.warn(`Model with ID ${modelId} not found`);
			}
			return acc;
		}, {});
		feedbackItem.meta.base_models = baseModels;

		let feedback = null;
		if (message?.feedbackId) {
			feedback = await updateFeedbackById(
				localStorage.token,
				message.feedbackId,
				feedbackItem
			).catch((error) => {
				console.error(error);
				return null;
			});
		}

		if (!feedback) {
			feedback = await createNewFeedback(localStorage.token, feedbackItem).catch((error) => {
				toast.error(`${error}`);
			});

			if (feedback) {
				updatedMessage.feedbackId = feedback.id;
			}
		}

		console.log(updatedMessage);
		saveMessage(message.id, updatedMessage);

		await tick();

		if (!details) {
			showRateComment = true;

			if (!updatedMessage.annotation?.tags && (message?.content ?? '') !== '') {
				// attempt to generate tags
				const tags = await generateTags(localStorage.token, message.model, messages, chatId).catch(
					(error) => {
						console.error(error);
						return [];
					}
				);
				console.log(tags);

				if (tags) {
					const taggedMessage = {
						...message,
						annotation: { ...(message?.annotation ?? {}), tags: tags }
					};
					feedbackItem.data = { ...feedbackItem.data, ...taggedMessage.annotation };

					saveMessage(message.id, taggedMessage);
					await updateFeedbackById(
						localStorage.token,
						taggedMessage.feedbackId,
						feedbackItem
					).catch((error) => {
						toast.error(`${error}`);
					});
				}
			}
		}

		feedbackLoading = false;
	};

	const deleteMessageHandler = async () => {
		deleteMessage(message.id);
	};

	$: if (!edit) {
		(async () => {
			await tick();
		})();
	}

	const buttonsWheelHandler = (event: WheelEvent) => {
		if (buttonsContainerElement) {
			if (buttonsContainerElement.scrollWidth <= buttonsContainerElement.clientWidth) {
				// If the container is not scrollable, horizontal scroll
				return;
			} else {
				event.preventDefault();

				if (event.deltaY !== 0) {
					// Adjust horizontal scroll position based on vertical scroll
					buttonsContainerElement.scrollLeft += event.deltaY;
				}
			}
		}
	};

	const contentCopyHandler = (e) => {
		if (contentContainerElement) {
			e.preventDefault();
			// Get the selected HTML
			const selection = window.getSelection();
			const range = selection.getRangeAt(0);
			const tempDiv = document.createElement('div');

			// Remove background, color, and font styles
			tempDiv.appendChild(range.cloneContents());

			tempDiv.querySelectorAll('table').forEach((table) => {
				table.style.borderCollapse = 'collapse';
				table.style.width = 'auto';
				table.style.tableLayout = 'auto';
			});

			tempDiv.querySelectorAll('th').forEach((th) => {
				th.style.whiteSpace = 'nowrap';
				th.style.padding = '4px 8px';
			});

			// Put cleaned HTML + plain text into clipboard
			e.clipboardData.setData('text/html', tempDiv.innerHTML);
			e.clipboardData.setData('text/plain', selection.toString());
		}
	};

	onMount(async () => {
		// console.log('ResponseMessage mounted');

		await tick();
		if (buttonsContainerElement) {
			buttonsContainerElement.addEventListener('wheel', buttonsWheelHandler);
		}

		if (contentContainerElement) {
			contentContainerElement.addEventListener('copy', contentCopyHandler);
		}
	});

	onDestroy(() => {
		if (buttonsContainerElement) {
			buttonsContainerElement.removeEventListener('wheel', buttonsWheelHandler);
		}

		if (contentContainerElement) {
			contentContainerElement.removeEventListener('copy', contentCopyHandler);
		}
	});
</script>

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete message?')}
	on:confirm={() => {
		deleteMessageHandler();
	}}
/>

{#key message.id}
	<div
		class=" flex w-full message-{message.id}"
		id="message-{message.id}"
		dir={$settings.chatDirection}
		style="scroll-margin-top: 3rem;"
	>
		<div class={`shrink-0 ltr:mr-2 rtl:ml-2 hidden @lg:flex mt-0.5 `}>
			<ProfileImage
				src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${headerModel?.id ?? message.model}&lang=${$i18n.language}`}
				className={'size-7 assistant-message-profile-image'}
			/>
		</div>

		<div class="flex-auto w-0 pl-1 relative">
			{#if !compactPreview}
				<Name>
					<Tooltip content={localizedModelName} placement="top-start">
						<span id="response-message-model-name" class="line-clamp-1 text-black dark:text-white">
							{localizedModelName}
						</span>
					</Tooltip>
					{#if answeredBy}
						<span class="text-xs font-normal text-gray-400 dark:text-gray-500 line-clamp-1">
							{$i18n.t('Answered by {{model}}', { model: answeredBy })}
						</span>
					{/if}
				</Name>
			{/if}

			<div>
				<div class="chat-{message.role} w-full min-w-full">
					<div>
						<TurnNotices notices={message?.notices ?? []} />
						{#if protocol === 'reasoning_only' && (model?.info?.meta?.capabilities?.status_updates ?? true)}
							<!-- No-tool / bezwaar turn: render parent reasoning blocks and
							     subagent groups interleaved by their `started_at` timestamp.
							     Each entry in `responseParts` is either a reasoning item
							     (rendered as a standalone ReasoningBullet) or a subagent
							     group (rendered via SubAgentGroup with pre-filtered events).
							     Turns with no subagents degrade gracefully — responseParts
							     contains only reasoning items and SubAgentGroup is never
							     mounted. Turns with no reasoning similarly degrade. -->
							<div class="standalone-reasoning flex flex-col gap-1 my-1">
								{#each responseParts as part (part.kind === 'reasoning' ? `r-${part.item.contentOffset}` : `g-${part.group_id}`)}
									{#if part.kind === 'reasoning'}
										<ReasoningBullet
											id={`standalone-reasoning-${part.item.contentOffset}`}
											summary={part.item.summary}
											body={part.item.body}
											attributes={part.item.attributes ?? {}}
										/>
									{:else}
										<SubAgentGroup group={part.group} />
									{/if}
								{/each}
							</div>
						{:else if shouldShowStatusHistory}
							<!-- [Gradient] One chronological timeline: reasoning, tool statuses
							     and the model's inter-tool prose, in stream order. The final
							     answer is NOT here — it stays in the ContentRenderer below, so
							     the tail renders exactly once. -->
							<StatusHistory
								id={`${chatId}-${message.id}-status`}
								statusHistory={timelineItems}
								messageDone={message?.done ?? false}
								sources={message.sources}
								{editCodeBlock}
								{model}
								onSourceClick={async (sid) => {
									if (citationsElement) {
										citationsElement?.showSourceModal(sid);
									}
								}}
							/>
							<!-- [Gradient] For tool-call turns (StatusHistory path), subagent
							     groups render below the dropdown sorted by their own
							     started_at. True interleaving inside the StatusHistory
							     accordion is deferred to a future phase. -->
							{#each responseParts.filter((p) => p.kind === 'subagent-group') as part (part.group_id)}
								<SubAgentGroup group={part.group} />
							{/each}
						{/if}

						{#if message?.action_required?.kind === 'connect' && consentLabels[message.action_required.provider]}
							<ConnectOneDrive provider={message.action_required.provider} />
						{/if}
						{#if message?.files && message.files?.filter( (f) => ['image', 'file'].includes(f.type) ).length > 0}
							<div
								class="my-1 w-full flex overflow-x-auto gap-2 flex-wrap"
								dir={$settings?.chatDirection ?? 'auto'}
							>
								{#each message.files.filter((f) => ['image', 'file'].includes(f.type)) as file}
									<div>
										{#if file.type === 'image' || (file?.content_type ?? '').startsWith('image/')}
											<Image src={file.url} alt={file.name || $i18n.t('Generated Image')} />
										{:else}
											<FileItem
												item={file}
												url={file.url}
												name={file.name}
												type={file.type}
												size={file?.size}
												small={true}
											/>
										{/if}
									</div>
								{/each}
							</div>
						{/if}

						{#if !readOnly && message?.embeds && message.embeds.length > 0}
							<div
								class="my-1 w-full flex overflow-x-auto gap-2 flex-wrap"
								id={`${message.id}-embeds-container`}
							>
								{#each message.embeds as embed, idx}
									<div class="my-2 w-full" id={`${message.id}-embeds-${idx}`}>
										<FullHeightIframe
											src={embed}
											allowScripts={true}
											allowForms={$settings?.iframeSandboxAllowForms ?? true}
											allowSameOrigin={$settings?.iframeSandboxAllowSameOrigin ?? false}
											allowPopups={true}
										/>
									</div>
								{/each}
							</div>
						{/if}

						{#if edit === true}
							<div
								class="w-full bg-gray-50 dark:bg-gray-800 rounded-3xl px-3 py-3 my-2 {($settings?.highContrastMode ??
								false)
									? 'focus-within:outline focus-within:outline-2 focus-within:-outline-offset-2 focus-within:outline-blue-500'
									: ''}"
							>
								{#if editedOutput}
									<!-- Structured output editor (visual + JSON toggle) -->
									<OutputEditView
										output={editedOutput}
										onChange={(updated) => {
											editedOutput = updated;
										}}
									/>
								{:else}
									<!-- Legacy textarea for messages without output -->
									<textarea
										id="message-edit-{message.id}"
										bind:this={editTextAreaElement}
										class=" bg-transparent outline-hidden focus-visible:outline-none! w-full resize-none text-[0.9375rem]"
										bind:value={editedContent}
										on:input={(e) => {
											const messagesContainer = document.getElementById('messages-container');
											const savedScrollTop = messagesContainer?.scrollTop ?? 0;
											const textarea = e.currentTarget;

											textarea.style.height = '';
											textarea.style.height = `${textarea.scrollHeight}px`;

											if (messagesContainer) messagesContainer.scrollTop = savedScrollTop;
										}}
										on:keydown={(e) => {
											if (e.key === 'Escape') {
												document.getElementById('close-edit-message-button')?.click();
											}

											const isCmdOrCtrlPressed = e.metaKey || e.ctrlKey;
											const isEnterPressed = e.key === 'Enter';

											if (isCmdOrCtrlPressed && isEnterPressed) {
												document.getElementById('confirm-edit-message-button')?.click();
											}
										}}
									></textarea>
								{/if}

								<div class=" mt-2 flex justify-between text-sm font-normal">
									<div>
										<button
											id="save-new-message-button"
											class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-100 px-2.5 text-xs font-normal text-gray-800 transition hover:bg-gray-100/70 disabled:opacity-60 dark:bg-gray-850 dark:text-white dark:hover:bg-gray-850/60"
											on:click={() => {
												saveAsCopyHandler();
											}}
										>
											{$i18n.t('Save As Copy')}
										</button>
									</div>

									<div class="flex space-x-1.5">
										<button
											id="close-edit-message-button"
											class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg px-2.5 text-xs font-normal text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60 dark:text-gray-400 dark:hover:bg-gray-850 dark:hover:text-white"
											on:click={() => {
												cancelEditMessage();
											}}
										>
											{$i18n.t('Cancel')}
										</button>

										{#if !message.user_id || message.user_id === $user?.id}
											<button
												id="confirm-edit-message-button"
												class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
												on:click={() => {
													editMessageConfirmHandler();
												}}
											>
												{$i18n.t('Save')}
											</button>
										{/if}
									</div>
								</div>
							</div>
						{/if}

						<div
							bind:this={contentContainerElement}
							class="w-full flex flex-col relative {edit ? 'hidden' : ''}"
							id="response-content-container"
						>
							{#if !hasResponseContent && !message.done && !message.error && !hasVisibleStatus}
								<Skeleton />
							{:else if (useBlockLayout ? renderedContent : hasResponseContent) && message.error !== true}
								<!-- always show message contents even if there's an error -->
								<!-- unless message.error === true which is legacy error handling, where the error message is stored in message.content -->
								<ContentRenderer
									id={`${chatId}-${message.id}`}
									content={renderedContent}
									output={useBlockLayout ? [] : message.output}
									{chatId}
									messageId={message.id}
									sources={message.sources}
									floatingButtons={message?.done &&
										!readOnly &&
										($settings?.showFloatingActionButtons ?? true)}
									save={(!readOnly && (!message.user_id || message.user_id === $user?.id)) ||
										(shareMode === 'continue' && message.user_id === $user?.id)}
									preview={!readOnly}
									{compactPreview}
									editCodeBlock={!readOnly && editCodeBlock}
									{topPadding}
									done={message?.done ?? false}
									allowEmbeds={!readOnly}
									{model}
									onTaskClick={async (e) => {
										console.log(e);
									}}
									{onToolCallResolved}
									onSourceClick={async (id) => {
										console.log(id);

										if (citationsElement) {
											citationsElement?.showSourceModal(id);
										}
									}}
									onSetInputText={(text) => {
										setInputText(text);
									}}
									onSave={({ raw, oldContent, newContent }) => {
										const sourceMessage = history.messages[message.id];
										if (sourceMessage.output?.length) {
											const updatedOutput = replaceOutputMessageText(
												sourceMessage.output,
												oldContent,
												newContent
											);
											if (updatedOutput !== sourceMessage.output) {
												sourceMessage.output = updatedOutput;
											} else {
												sourceMessage.content = sourceMessage.content.replace(raw, () =>
													raw.replace(oldContent, () => newContent)
												);
											}
										} else {
											sourceMessage.content = sourceMessage.content.replace(raw, () =>
												raw.replace(oldContent, () => newContent)
											);
										}

										updateChat();
									}}
								/>
							{/if}

							<!-- [Gradient] The caret marks text being written, so it waits for the answer's first
							     text: while the model thinks or calls tools, the shimmering line above says so. -->
							{#if !message.done && !message.error && hasAnswerText}
								<div class="text-[0.9375rem] leading-relaxed">
									<span
										class="inline-block w-[0.125rem] h-3.5 bg-gray-400 dark:bg-gray-500 ml-0.5 animate-cursor-pulse align-text-bottom"
									></span>
								</div>
							{/if}

							{#if message?.error}
								<Error
									content={message?.error?.content ?? message.content}
									error={message?.error}
									model={message?.model ?? null}
								/>
							{/if}

							{#if (message?.uiBlocks ?? []).length > 0}
								<PresentUIDispatcher
									blocks={message.uiBlocks}
									messageId={message.id}
									messageDone={message.done ?? false}
								/>
							{/if}

							{#if (message?.sources || message?.citations) && (model?.info?.meta?.capabilities?.citations ?? true)}
								<Citations
									bind:this={citationsElement}
									id={message?.id}
									{chatId}
									sources={message?.sources ?? message?.citations}
									text={visibleResponseContent}
									{readOnly}
								/>
							{/if}

							{#if message.code_executions}
								<CodeExecutions codeExecutions={message.code_executions} />
							{/if}
						</div>
					</div>
				</div>

				{#if compactPreview && message.timestamp}
					<div class="mt-0.5 flex justify-start whitespace-nowrap text-gray-600 dark:text-gray-500">
						<Tooltip
							className="flex self-center"
							content={formatMessageTimestampFull(message.timestamp * 1000, $i18n.language)}
							placement="bottom"
						>
							<time
								datetime={new Date(message.timestamp * 1000).toISOString()}
								class="ml-1 shrink-0 whitespace-nowrap text-[0.6875rem] tabular-nums text-gray-400 dark:text-gray-600 select-none"
							>
								{formatMessageTimestamp(message.timestamp * 1000, $i18n.language)}
							</time>
						</Tooltip>
					</div>
				{:else if !edit}
					<div
						bind:this={buttonsContainerElement}
						class="flex items-center justify-start overflow-x-auto whitespace-nowrap buttons {($settings?.highContrastMode ??
						false)
							? 'text-gray-600 dark:text-gray-500'
							: 'text-gray-600/80 dark:text-gray-500/70'} mt-0.5 [&>*]:shrink-0"
					>
						{#if message.done || siblings.length > 1}
							{#if message.done && onInsertToNote && visibleResponseContent}
								<Tooltip content={$i18n.t('Insert into note')} placement="bottom">
									<button
										aria-label={$i18n.t('Insert into note')}
										class="{isLastMessage || ($settings?.highContrastMode ?? false)
											? 'visible'
											: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition"
										on:click={() => {
											onInsertToNote?.(visibleResponseContent);
										}}
									>
										<svg
											class="size-4"
											aria-hidden="true"
											viewBox="0 0 24 24"
											stroke-width="1.5"
											fill="none"
											xmlns="http://www.w3.org/2000/svg"
										>
											<path
												d="M19 19L6 6M6 6L6 18.48M6 6L18.48 6"
												stroke="currentColor"
												stroke-linecap="round"
												stroke-linejoin="round"
											/>
										</svg>
									</button>
								</Tooltip>
							{/if}

							{#if siblings.length > 1}
								<div class="flex self-center min-w-fit" dir="ltr">
									<button
										aria-label={$i18n.t('Previous message')}
										class="self-center inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-gray-400 hover:text-gray-600 rounded-md transition"
										on:click={() => {
											showPreviousMessage(message);
										}}
									>
										<ChevronRightIcon className="size-4 rotate-180" />
									</button>

									{#if messageIndexEdit}
										<div
											class="text-sm flex justify-center font-normal self-center dark:text-gray-500 min-w-fit"
										>
											<input
												id="message-index-input-{message.id}"
												type="number"
												value={siblings.indexOf(message.id) + 1}
												min="1"
												max={siblings.length}
												on:focus={(e) => {
													e.target.select();
												}}
												on:blur={(e) => {
													gotoMessage(message, e.target.value - 1);
													messageIndexEdit = false;
												}}
												on:keydown={(e) => {
													if (e.key === 'Enter') {
														gotoMessage(message, e.target.value - 1);
														messageIndexEdit = false;
													}
												}}
												class="bg-transparent font-normal self-center dark:text-gray-500 min-w-fit outline-hidden"
											/>/{siblings.length}
										</div>
									{:else}
										<!-- svelte-ignore a11y-no-static-element-interactions -->
										<div
											class="text-sm tracking-widest font-normal self-center dark:text-gray-500 min-w-fit"
											on:dblclick={async () => {
												messageIndexEdit = true;

												await tick();
												const input = document.getElementById(`message-index-input-${message.id}`);
												if (input) {
													input.focus();
													input.select();
												}
											}}
										>
											{siblings.indexOf(message.id) + 1}/{siblings.length}
										</div>
									{/if}

									<button
										class="self-center inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-gray-400 hover:text-gray-600 rounded-md transition"
										on:click={() => {
											showNextMessage(message);
										}}
										aria-label={$i18n.t('Next message')}
									>
										<ChevronRightIcon className="size-4" />
									</button>
								</div>
							{/if}

							{#if message.done}
								{#if !readOnly && ANSWERS_EDITABLE}
									{#if $user?.role === 'user' ? ($user?.permissions?.chat?.edit ?? true) : true}
										<Tooltip content={$i18n.t('Edit')} placement="bottom">
											<button
												aria-label={$i18n.t('Edit')}
												class="{isLastMessage || ($settings?.highContrastMode ?? false)
													? 'visible'
													: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition"
												on:click={() => {
													editMessageHandler();
												}}
											>
												<EditPencilIcon />
											</button>
										</Tooltip>
									{/if}
								{/if}

								<Tooltip content={$i18n.t('Copy')} placement="bottom">
									<button
										aria-label={$i18n.t('Copy')}
										class="{isLastMessage || ($settings?.highContrastMode ?? false)
											? 'visible'
											: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition copy-response-button"
										on:click={() => {
											copyToClipboard(visibleResponseContent, message.sources ?? []);
										}}
									>
										<CopyIcon />
									</button>
								</Tooltip>

								<Dropdown bind:show={showDownloadMenu} align="start" sideOffset={4}>
									<Tooltip content={$i18n.t('Download')} placement="bottom">
										<button
											aria-label={$i18n.t('Download')}
											class="{isLastMessage || ($settings?.highContrastMode ?? false)
												? 'visible'
												: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition"
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												fill="none"
												aria-hidden="true"
												viewBox="0 0 24 24"
												stroke-width="2.3"
												stroke="currentColor"
												class="w-4 h-4"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3"
												/>
											</svg>
										</button>
									</Tooltip>

									<div slot="content">
										<div
											class="w-52 rounded-xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
										>
											<button
												class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg select-none w-full"
												on:click={() => {
													downloadMessageAsText();
													showDownloadMenu = false;
												}}
											>
												<div class="flex items-center line-clamp-1">
													{$i18n.t('Plain text (.txt)')}
												</div>
											</button>
											<button
												class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg select-none w-full"
												on:click={() => {
													downloadMessageAsMarkdown();
													showDownloadMenu = false;
												}}
											>
												<div class="flex items-center line-clamp-1">
													{$i18n.t('Markdown (.md)')}
												</div>
											</button>
											<button
												class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg select-none w-full"
												on:click={() => {
													downloadMessageAsPdf();
													showDownloadMenu = false;
												}}
											>
												<div class="flex items-center line-clamp-1">
													{$i18n.t('PDF document (.pdf)')}
												</div>
											</button>
											{#if $config?.features?.enable_docx_export ?? true}
												<button
													class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg select-none w-full"
													on:click={() => {
														downloadMessageAsDocx();
														showDownloadMenu = false;
													}}
												>
													<div class="flex items-center line-clamp-1">
														{$i18n.t('Word document (.docx)')}
													</div>
												</button>
											{/if}
										</div>
									</div>
								</Dropdown>

								{#if isFeatureEnabled('read_aloud') && !readOnly && ($user?.role === 'admin' || ($user?.permissions?.chat?.tts ?? true))}
									<Tooltip content={$i18n.t('Read Aloud')} placement="bottom">
										<button
											aria-label={$i18n.t('Read Aloud')}
											id="speak-button-{message.id}"
											class="{isLastMessage || ($settings?.highContrastMode ?? false)
												? 'visible'
												: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition"
											on:click={() => {
												if (!loadingSpeech) {
													if (speaking) {
														stopAudio();
													} else {
														speak();
													}
												}
											}}
										>
											{#if loadingSpeech}
												<svg
													class="size-4 animate-spin"
													aria-hidden="true"
													stroke-width="1.5"
													viewBox="0 0 24 24"
													fill="none"
													xmlns="http://www.w3.org/2000/svg"
												>
													<path
														d="M21.8883 13.5C21.1645 18.3113 17.013 22 12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C16.1006 2 19.6248 4.46819 21.1679 8"
														stroke="currentColor"
														stroke-linecap="round"
														stroke-linejoin="round"
													/>
													<path
														d="M17 8H21.4C21.7314 8 22 7.73137 22 7.4V3"
														stroke="currentColor"
														stroke-linecap="round"
														stroke-linejoin="round"
													/>
												</svg>
											{:else if speaking}
												<svg
													class="size-4"
													aria-hidden="true"
													viewBox="0 0 24 24"
													stroke-width="1.5"
													fill="none"
													xmlns="http://www.w3.org/2000/svg"
												>
													<path
														d="M18 14L20.0005 12M22 10L20.0005 12M20.0005 12L18 10M20.0005 12L22 14"
														stroke="currentColor"
														stroke-linecap="round"
														stroke-linejoin="round"
													/>
													<path
														d="M2 13.8571V10.1429C2 9.03829 2.89543 8.14286 4 8.14286H6.9C7.09569 8.14286 7.28708 8.08544 7.45046 7.97772L13.4495 4.02228C14.1144 3.5839 15 4.06075 15 4.85714V19.1429C15 19.9392 14.1144 20.4161 13.4495 19.9777L7.45046 16.0223C7.28708 15.9146 7.09569 15.8571 6.9 15.8571H4C2.89543 15.8571 2 14.9617 2 13.8571Z"
														stroke="currentColor"
													/>
												</svg>
											{:else}
												<svg
													class="size-4"
													aria-hidden="true"
													viewBox="0 0 24 24"
													stroke-width="1.5"
													fill="none"
													xmlns="http://www.w3.org/2000/svg"
												>
													<path
														d="M1 13.8571V10.1429C1 9.03829 1.89543 8.14286 3 8.14286H5.9C6.09569 8.14286 6.28708 8.08544 6.45046 7.97772L12.4495 4.02228C13.1144 3.5839 14 4.06075 14 4.85714V19.1429C14 19.9392 13.1144 20.4161 12.4495 19.9777L6.45046 16.0223C6.28708 15.9146 6.09569 15.8571 5.9 15.8571H3C1.89543 15.8571 1 14.9617 1 13.8571Z"
														stroke="currentColor"
													/>
													<path
														d="M17.5 7.5C17.5 7.5 19 9 19 11.5C19 14 17.5 15.5 17.5 15.5"
														stroke="currentColor"
														stroke-linecap="round"
														stroke-linejoin="round"
													/>
													<path
														d="M20.5 4.5C20.5 4.5 23 7 23 11.5C23 16 20.5 18.5 20.5 18.5"
														stroke="currentColor"
														stroke-linecap="round"
														stroke-linejoin="round"
													/>
												</svg>
											{/if}
										</button>
									</Tooltip>
								{/if}

								{#if message.usage}
									<Tooltip
										content={message.usage
											? `<pre>${sanitizeResponseContent(
													JSON.stringify(message.usage, null, 2)
														.replace(/"([^(")"]+)":/g, '$1:')
														.slice(1, -1)
														.split('\n')
														.map((line) => line.slice(2))
														.map((line) => (line.endsWith(',') ? line.slice(0, -1) : line))
														.join('\n')
												)}</pre>`
											: ''}
										placement="bottom"
									>
										<button
											aria-hidden="true"
											class=" {isLastMessage || ($settings?.highContrastMode ?? false)
												? 'visible'
												: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition whitespace-pre-wrap"
											on:click={() => {
												console.log(message);
											}}
											id="info-{message.id}"
										>
											<svg
												class="size-4"
												aria-hidden="true"
												stroke-width="1.5"
												viewBox="0 0 24 24"
												fill="none"
												xmlns="http://www.w3.org/2000/svg"
											>
												<path
													d="M12 11.5V16.5"
													stroke="currentColor"
													stroke-linecap="round"
													stroke-linejoin="round"
												/>
												<path
													d="M12 7.51L12.01 7.49889"
													stroke="currentColor"
													stroke-linecap="round"
													stroke-linejoin="round"
												/>
												<path
													d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22Z"
													stroke="currentColor"
													stroke-linecap="round"
													stroke-linejoin="round"
												/>
											</svg>
										</button>
									</Tooltip>
								{/if}

								{#if $user && message.done && forkHandler && ($user?.role === 'admin' || ($user?.permissions?.chat?.import ?? true))}
									<Tooltip content={$i18n.t('Fork chat')} placement="bottom">
										<button
											aria-label={$i18n.t('Fork chat')}
											class="{isLastMessage || ($settings?.highContrastMode ?? false)
												? 'visible'
												: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition"
											on:click={() => {
												forkHandler?.(message.id);
											}}
										>
											<svg
												class="size-4 -scale-x-100 -scale-y-100 -rotate-90"
												aria-hidden="true"
												viewBox="0 0 24 24"
												stroke-width="1.5"
												fill="none"
												xmlns="http://www.w3.org/2000/svg"
											>
												<path
													d="M17 7C18.1046 7 19 6.10457 19 5C19 3.89543 18.1046 3 17 3C15.8954 3 15 3.89543 15 5C15 6.10457 15.8954 7 17 7Z"
													stroke="currentColor"
													stroke-linecap="round"
													stroke-linejoin="round"
												/>
												<path
													d="M7 7C8.10457 7 9 6.10457 9 5C9 3.89543 8.10457 3 7 3C5.89543 3 5 3.89543 5 5C5 6.10457 5.89543 7 7 7Z"
													stroke="currentColor"
													stroke-linecap="round"
													stroke-linejoin="round"
												/>
												<path
													d="M7 21C8.10457 21 9 20.1046 9 19C9 17.8954 8.10457 17 7 17C5.89543 17 5 17.8954 5 19C5 20.1046 5.89543 21 7 21Z"
													stroke="currentColor"
													stroke-linecap="round"
													stroke-linejoin="round"
												/>
												<path
													d="M7 7V17"
													stroke="currentColor"
													stroke-linecap="round"
													stroke-linejoin="round"
												/>
												<path
													d="M17 7V8C17 10.5 15 11 15 11L9 13C9 13 7 13.5 7 16V17"
													stroke="currentColor"
													stroke-linecap="round"
													stroke-linejoin="round"
												/>
											</svg>
										</button>
									</Tooltip>
								{/if}

								{#if (!readOnly && (!message.user_id || message.user_id === $user?.id)) || (shareMode === 'continue' && message.user_id === $user?.id)}
									{#if !readOnly && !$temporaryChatEnabled && ($config?.features.enable_message_rating ?? true) && ($user?.role === 'admin' || ($user?.permissions?.chat?.rate_response ?? true))}
										<Tooltip content={$i18n.t('Good Response')} placement="bottom">
											<button
												aria-label={$i18n.t('Good Response')}
												class="{isLastMessage || ($settings?.highContrastMode ?? false)
													? 'visible'
													: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg {(
													message?.annotation?.rating ?? ''
												).toString() === '1'
													? 'bg-gray-100 dark:bg-gray-800'
													: ''} dark:hover:text-gray-400 hover:text-gray-600 transition disabled:cursor-progress disabled:hover:bg-transparent"
												disabled={feedbackLoading}
												on:click={async (e) => {
													e.stopPropagation();
													await feedbackHandler(1);
													window.setTimeout(() => {
														document
															.getElementById(`message-feedback-${message.id}`)
															?.scrollIntoView();
													}, 0);
												}}
											>
												<svg
													class="size-4"
													aria-hidden="true"
													stroke-width="1.5"
													viewBox="0 0 24 24"
													fill="none"
													xmlns="http://www.w3.org/2000/svg"
												>
													<path
														d="M16.4724 20H4.1C3.76863 20 3.5 19.7314 3.5 19.4V9.6C3.5 9.26863 3.76863 9 4.1 9H6.86762C7.57015 9 8.22116 8.6314 8.5826 8.02899L11.293 3.51161C11.8779 2.53688 13.2554 2.44422 13.9655 3.33186C14.3002 3.75025 14.4081 4.30635 14.2541 4.81956L13.2317 8.22759C13.1162 8.61256 13.4045 9 13.8064 9H18.3815C19.7002 9 20.658 10.254 20.311 11.5262L18.4019 18.5262C18.1646 19.3964 17.3743 20 16.4724 20Z"
														stroke="currentColor"
														stroke-linecap="round"
													/>
													<path
														d="M7 20L7 9"
														stroke="currentColor"
														stroke-linecap="round"
														stroke-linejoin="round"
													/>
												</svg>
											</button>
										</Tooltip>

										<Tooltip content={$i18n.t('Bad Response')} placement="bottom">
											<button
												aria-label={$i18n.t('Bad Response')}
												class="{isLastMessage || ($settings?.highContrastMode ?? false)
													? 'visible'
													: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg {(
													message?.annotation?.rating ?? ''
												).toString() === '-1'
													? 'bg-gray-100 dark:bg-gray-800'
													: ''} dark:hover:text-gray-400 hover:text-gray-600 transition disabled:cursor-progress disabled:hover:bg-transparent"
												disabled={feedbackLoading}
												on:click={async (e) => {
													e.stopPropagation();
													await feedbackHandler(-1);
													window.setTimeout(() => {
														document
															.getElementById(`message-feedback-${message.id}`)
															?.scrollIntoView();
													}, 0);
												}}
											>
												<svg
													class="size-4"
													aria-hidden="true"
													stroke-width="1.5"
													viewBox="0 0 24 24"
													fill="none"
													xmlns="http://www.w3.org/2000/svg"
												>
													<path
														d="M16.4724 3.5H4.1C3.76863 3.5 3.5 3.76863 3.5 4.1V13.9C3.5 14.2314 3.76863 14.5 4.1 14.5H6.86762C7.57015 14.5 8.22116 14.8686 8.5826 15.471L11.293 19.9884C11.8779 20.9631 13.2554 21.0558 13.9655 20.1681C14.3002 19.7497 14.4081 19.1937 14.2541 18.6804L13.2317 15.2724C13.1162 14.8874 13.4045 14.5 13.8064 14.5H18.3815C19.7002 14.5 20.658 13.246 20.311 11.9738L18.4019 4.97376C18.1646 4.10364 17.3743 3.5 16.4724 3.5Z"
														stroke="currentColor"
														stroke-linecap="round"
													/>
													<path
														d="M7 14.5L7 3.5"
														stroke="currentColor"
														stroke-linecap="round"
														stroke-linejoin="round"
													/>
												</svg>
											</button>
										</Tooltip>
									{/if}

									{#if isLastMessage && canContinueResponse($config, $user)}
										<Tooltip content={$i18n.t('Continue Response')} placement="bottom">
											<button
												aria-label={$i18n.t('Continue Response')}
												type="button"
												id="continue-response-button"
												class="{isLastMessage || ($settings?.highContrastMode ?? false)
													? 'visible'
													: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition"
												on:click={() => {
													continueResponse(message);
												}}
											>
												<svg
													class="size-4"
													aria-hidden="true"
													stroke-width="1.5"
													viewBox="0 0 24 24"
													fill="none"
													xmlns="http://www.w3.org/2000/svg"
												>
													<path
														d="M6.90588 4.53682C6.50592 4.2998 6 4.58808 6 5.05299V18.947C6 19.4119 6.50592 19.7002 6.90588 19.4632L18.629 12.5162C19.0211 12.2838 19.0211 11.7162 18.629 11.4838L6.90588 4.53682Z"
														stroke="currentColor"
														stroke-linecap="round"
														stroke-linejoin="round"
													/>
												</svg>
											</button>
										</Tooltip>
									{/if}
								{/if}

								{#if (!readOnly && (!message.user_id || message.user_id === $user?.id)) || shareMode === 'continue'}
									{#if $user?.role === 'admin' || ($user?.permissions?.chat?.regenerate_response ?? true)}
										{#if $settings?.regenerateMenu ?? true}
											<button
												type="button"
												class="hidden regenerate-response-button"
												on:click={() => {
													showRateComment = false;
													regenerateResponse(message);

													(model?.actions ?? []).forEach((action) => {
														dispatch('action', {
															id: action.id,
															event: {
																id: 'regenerate-response',
																data: {
																	messageId: message.id
																}
															}
														});
													});
												}}
											/>

											<RegenerateMenu
												onRegenerate={(prompt = null) => {
													showRateComment = false;
													regenerateResponse(message, prompt);

													(model?.actions ?? []).forEach((action) => {
														dispatch('action', {
															id: action.id,
															event: {
																id: 'regenerate-response',
																data: {
																	messageId: message.id
																}
															}
														});
													});
												}}
											>
												<Tooltip content={$i18n.t('Regenerate')} placement="bottom">
													<button
														type="button"
														aria-label={$i18n.t('Regenerate')}
														class="{isLastMessage || ($settings?.highContrastMode ?? false)
															? 'visible'
															: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition"
													>
														<svg
															class="size-4"
															aria-hidden="true"
															stroke-width="1.5"
															viewBox="-2 -2 28 28"
															fill="none"
															xmlns="http://www.w3.org/2000/svg"
														>
															<path
																d="M21.1679 8C19.6247 4.46819 16.1006 2 11.9999 2C6.81459 2 2.55104 5.94668 2.04932 11"
																stroke="currentColor"
																stroke-linecap="round"
																stroke-linejoin="round"
															/>
															<path
																d="M17 8H21.4C21.7314 8 22 7.73137 22 7.4V3"
																stroke="currentColor"
																stroke-linecap="round"
																stroke-linejoin="round"
															/>
															<path
																d="M2.88146 16C4.42458 19.5318 7.94874 22 12.0494 22C17.2347 22 21.4983 18.0533 22 13"
																stroke="currentColor"
																stroke-linecap="round"
																stroke-linejoin="round"
															/>
															<path
																d="M7.04932 16H2.64932C2.31795 16 2.04932 16.2686 2.04932 16.6V21"
																stroke="currentColor"
																stroke-linecap="round"
																stroke-linejoin="round"
															/>
														</svg>
													</button>
												</Tooltip>
											</RegenerateMenu>
										{:else}
											<Tooltip content={$i18n.t('Regenerate')} placement="bottom">
												<button
													type="button"
													aria-label={$i18n.t('Regenerate')}
													class="{isLastMessage || ($settings?.highContrastMode ?? false)
														? 'visible'
														: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition regenerate-response-button"
													on:click={() => {
														showRateComment = false;
														regenerateResponse(message);

														(model?.actions ?? []).forEach((action) => {
															dispatch('action', {
																id: action.id,
																event: {
																	id: 'regenerate-response',
																	data: {
																		messageId: message.id
																	}
																}
															});
														});
													}}
												>
													<svg
														class="size-4"
														aria-hidden="true"
														stroke-width="1.5"
														viewBox="-2 -2 28 28"
														fill="none"
														xmlns="http://www.w3.org/2000/svg"
													>
														<path
															d="M21.1679 8C19.6247 4.46819 16.1006 2 11.9999 2C6.81459 2 2.55104 5.94668 2.04932 11"
															stroke="currentColor"
															stroke-linecap="round"
															stroke-linejoin="round"
														/>
														<path
															d="M17 8H21.4C21.7314 8 22 7.73137 22 7.4V3"
															stroke="currentColor"
															stroke-linecap="round"
															stroke-linejoin="round"
														/>
														<path
															d="M2.88146 16C4.42458 19.5318 7.94874 22 12.0494 22C17.2347 22 21.4983 18.0533 22 13"
															stroke="currentColor"
															stroke-linecap="round"
															stroke-linejoin="round"
														/>
														<path
															d="M7.04932 16H2.64932C2.31795 16 2.04932 16.2686 2.04932 16.6V21"
															stroke="currentColor"
															stroke-linecap="round"
															stroke-linejoin="round"
														/>
													</svg>
												</button>
											</Tooltip>
										{/if}
									{/if}
								{/if}

								{#if (!readOnly && (!message.user_id || message.user_id === $user?.id)) || (shareMode === 'continue' && message.user_id === $user?.id)}
									{#each (!readOnly && model?.actions) || [] as action}
										<Tooltip
											content={resolveLocalizedFunction(
												action,
												$localizedFunctions,
												$i18n.language
											)}
											placement="bottom"
										>
											<button
												type="button"
												aria-label={resolveLocalizedFunction(
													action,
													$localizedFunctions,
													$i18n.language
												)}
												class="{isLastMessage || ($settings?.highContrastMode ?? false)
													? 'visible'
													: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition"
												on:click={() => {
													actionMessage(action.id, message);
												}}
											>
												{#if action?.icon}
													<div class="size-4">
														<img
															src={action.icon}
															class="w-4 h-4 {action.icon.includes('data:image/svg')
																? 'dark:invert-[80%]'
																: ''}"
															style="fill: currentColor;"
															alt={resolveLocalizedFunction(
																action,
																$localizedFunctions,
																$i18n.language
															)}
															draggable="false"
														/>
													</div>
												{:else}
													<Sparkles />
												{/if}
											</button>
										</Tooltip>
									{/each}

									{#if $user?.role === 'admin' || ($user?.permissions?.chat?.delete_message ?? true)}
										{#if (!readOnly || ($user?.role === 'admin' && $config?.features?.enable_admin_chat_access)) && siblings.length > 1}
											<Tooltip content={$i18n.t('Delete')} placement="bottom">
												<button
													type="button"
													aria-label={$i18n.t('Delete')}
													id="delete-response-button"
													class="{isLastMessage || ($settings?.highContrastMode ?? false)
														? 'visible'
														: 'hover-reveal'} inline-flex size-7 shrink-0 items-center justify-center p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-gray-400 hover:text-gray-600 transition disabled:opacity-50 disabled:hover:bg-transparent"
													disabled={!allowDelete}
													on:click={(e) => {
														if (!allowDelete) {
															return;
														}
														if (e.shiftKey) {
															deleteMessageHandler();
														} else {
															showDeleteConfirm = true;
														}
													}}
												>
													<TrashIcon />
												</button>
											</Tooltip>
										{/if}
									{/if}

									{#if message.timestamp}
										<Tooltip
											className="flex self-center"
											content={formatMessageTimestampFull(message.timestamp * 1000, $i18n.language)}
											placement="bottom"
										>
											<time
												datetime={new Date(message.timestamp * 1000).toISOString()}
												class="hover-reveal ml-1 shrink-0 whitespace-nowrap text-[0.6875rem] tabular-nums text-gray-400 dark:text-gray-600 select-none"
											>
												{formatMessageTimestamp(message.timestamp * 1000, $i18n.language)}
											</time>
										</Tooltip>
									{/if}
								{/if}
							{/if}
						{/if}
					</div>

					{#if message.done && showRateComment}
						{#key message?.annotation?.rating}
							<RateComment
								bind:message
								bind:show={showRateComment}
								on:save={async (e) => {
									await feedbackHandler(null, {
										...e.detail
									});
								}}
							/>
						{/key}
					{/if}

					{#if (isLastMessage || ($settings?.keepFollowUpPrompts ?? false)) && message.done && !readOnly && (message?.followUps ?? []).length > 0}
						<div class="my-2.5" in:fade={{ duration: 100 }}>
							<FollowUps
								followUps={message?.followUps}
								onClick={(prompt) => {
									if ($settings?.insertFollowUpPrompt ?? false) {
										// Insert the follow-up prompt into the input box
										setInputText(prompt);
									} else {
										// Submit the follow-up prompt directly
										submitMessage(message?.id, prompt);
									}
								}}
							/>
						</div>
					{/if}
				{/if}
			</div>
		</div>
	</div>
{/key}

<style>
	.buttons::-webkit-scrollbar {
		display: none; /* for Chrome, Safari and Opera */
	}

	.buttons {
		-ms-overflow-style: none; /* IE and Edge */
		scrollbar-width: none; /* Firefox */
	}

	/* [Gradient] A streaming turn's first thought renders here and is remounted as
	   the StatusHistory header when the first tool status arrives. The header does
	   not slide in, so a line that is still sliding when it is swapped snaps to a
	   new spot. It appears in place, as the header does. */
	.standalone-reasoning :global(.status-description) {
		animation: none;
	}
</style>
