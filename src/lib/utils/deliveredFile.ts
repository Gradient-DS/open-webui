// [Gradient] Delivered Office files stay in agent storage.
import fileSaver from 'file-saver';

import { WEBUI_API_BASE_URL } from '$lib/constants';

const { saveAs } = fileSaver;

export type OfficeAttachment = {
	type: 'office';
	name: string;
	content_type: string;
	size: number;
	thread_id: string;
	element_id: string;
	pages: number;
	version: number;
	edits?: string | null;
};

export type DeliveredFile = OfficeAttachment;

export const isDeliveredFile = (file: any): file is DeliveredFile => {
	if (file?.type === 'office') {
		return (
			typeof file.name === 'string' &&
			typeof file.content_type === 'string' &&
			Number.isInteger(file.size) &&
			file.size >= 0 &&
			typeof file.element_id === 'string' &&
			file.element_id.length > 0 &&
			Number.isInteger(file.pages) &&
			file.pages >= 0 &&
			Number.isInteger(file.version) &&
			file.version >= 1
		);
	}
	return false;
};

const officeBlob = async (file: OfficeAttachment, chatId: string, part: string): Promise<Blob> => {
	const response = await fetch(
		`${WEBUI_API_BASE_URL}/chats/${encodeURIComponent(chatId)}/office/${encodeURIComponent(file.element_id)}/${part}`,
		{ headers: { Authorization: `Bearer ${localStorage.token}` }, credentials: 'include' }
	);
	if (!response.ok) throw new Error(`Office download failed: ${response.status}`);
	return response.blob();
};

/** Each page as an object URL, loaded with the user's token; the caller revokes them. Throws when one fails. */
export const loadPages = async (file: DeliveredFile, chatId: string): Promise<string[]> => {
	const pages = await Promise.all(
		Array.from({ length: file.pages }, (_, i) => officeBlob(file, chatId, `page-${i + 1}`))
	);
	return pages.map((page) => URL.createObjectURL(page));
};

/** [Gradient] The largest delivered picture shown in the chat; a larger one stays a card. */
const SHOWN_IMAGE_BYTES = 10 * 1024 * 1024;

/** True for a delivered PNG or JPEG small enough to show in the chat. */
export const isShownImage = (file: DeliveredFile): boolean =>
	['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml'].includes(
		file.content_type
	) && file.size <= SHOWN_IMAGE_BYTES;

/** The delivered picture as a data URL, loaded with the user's token. Throws when it cannot be read. */
export const loadImage = async (file: DeliveredFile, chatId: string): Promise<string> => {
	const data = await officeBlob(file, chatId, 'file');
	return await new Promise((resolve, reject) => {
		const reader = new FileReader();
		reader.onload = () => resolve(reader.result as string);
		reader.onerror = () => reject(reader.error);
		reader.readAsDataURL(new Blob([data], { type: file.content_type }));
	});
};

/** A loaded picture as PNG for the clipboard, drawn on a canvas unless it is one already;
 * an animation gives its first frame, an SVG is drawn at its own size. */
export const pngBlob = async (dataUrl: string): Promise<Blob> => {
	if (dataUrl.startsWith('data:image/png')) return await (await fetch(dataUrl)).blob();
	const image = new Image();
	image.src = dataUrl;
	await image.decode();
	const canvas = document.createElement('canvas');
	canvas.width = image.naturalWidth || 800;
	canvas.height = image.naturalHeight || 600;
	canvas.getContext('2d')?.drawImage(image, 0, 0, canvas.width, canvas.height);
	return await new Promise((resolve, reject) =>
		canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('no PNG'))), 'image/png')
	);
};

/** [Gradient] The largest delivered text file the panel shows. */
const SHOWN_TEXT_BYTES = 5 * 1024 * 1024;
const TEXT_TYPES = ['text/csv', 'text/plain', 'text/markdown', 'application/json'];

/** True for a delivered CSV, text, markdown or JSON file small enough to preview. */
export const isTextPreview = (file: DeliveredFile): boolean =>
	TEXT_TYPES.includes(file.content_type) && file.size <= SHOWN_TEXT_BYTES;

/** The delivered file as text. Throws when it cannot be read. */
export const loadText = async (file: DeliveredFile, chatId: string): Promise<string> =>
	await (await officeBlob(file, chatId, 'file')).text();

/** Throws when the file cannot be read. */
export const downloadDeliveredFile = async (file: DeliveredFile, chatId: string): Promise<void> => {
	const data = await officeBlob(file, chatId, 'file');
	saveAs(data, file.name);
};

/** The file's type as its extension, e.g. PPTX. */
export const fileKind = (file: DeliveredFile): string =>
	(file.name.split('.').pop() ?? '').toUpperCase();

export const newestFileVersionIndex = (
	contents: Array<{ file?: DeliveredFile }>,
	requested: DeliveredFile
): number => {
	const files = new Map(
		contents.flatMap(({ file }) => (file ? [[file.element_id, file] as const] : []))
	);
	files.set(requested.element_id, requested);
	const root = (file: DeliveredFile): string | null => {
		let id = file.element_id;
		const visited = new Set<string>();
		while (!visited.has(id)) {
			visited.add(id);
			const parent = files.get(id)?.edits;
			if (!parent) return id;
			id = parent;
		}
		return null;
	};
	const requestedRoot = root(requested);
	if (requestedRoot === null) return -1;
	let selected = -1;
	let version = 0;
	contents.forEach(({ file }, index) => {
		if (file && root(file) === requestedRoot && file.version >= version) {
			selected = index;
			version = file.version;
		}
	});
	return selected;
};
