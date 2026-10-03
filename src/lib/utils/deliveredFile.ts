// [Gradient] Delivered Office files stay in agent storage; old messages may still name OWUI Files.
import fileSaver from 'file-saver';

import { WEBUI_API_BASE_URL } from '$lib/constants';
import { getFileContentById } from '$lib/apis/files';

const { saveAs } = fileSaver;

type LegacyDeliveredFile = {
	type: 'file';
	id: string;
	name: string;
	url: string;
	pages: string[];
};

export type OfficeAttachment = {
	type: 'office';
	name: string;
	content_type: string;
	size: number;
	thread_id: string;
	element_id: string;
	pages: number;
	version: number;
};

export type DeliveredFile = OfficeAttachment | LegacyDeliveredFile;

export const isDeliveredFile = (file: any): file is DeliveredFile => {
	if (file?.type === 'office') {
		return (
			typeof file.name === 'string' &&
			typeof file.content_type === 'string' &&
			Number.isInteger(file.size) &&
			file.size >= 0 &&
			typeof file.thread_id === 'string' &&
			file.thread_id.length > 0 &&
			typeof file.element_id === 'string' &&
			file.element_id.length > 0 &&
			Number.isInteger(file.pages) &&
			file.pages >= 0 &&
			Number.isInteger(file.version) &&
			file.version >= 1
		);
	}
	return (
		file?.type === 'file' &&
		typeof file.id === 'string' &&
		Array.isArray(file.pages) &&
		file.pages.length > 0
	);
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
	const pages =
		file.type === 'office'
			? await Promise.all(
					Array.from({ length: file.pages }, (_, i) => officeBlob(file, chatId, `page-${i + 1}`))
				)
			: await Promise.all(
					file.pages.map(
						async (id) =>
							new Blob([(await getFileContentById(id)) as ArrayBuffer], { type: 'image/png' })
					)
				);
	return pages.map((page) => URL.createObjectURL(page));
};

/** Throws when the file cannot be read. */
export const downloadDeliveredFile = async (file: DeliveredFile, chatId: string): Promise<void> => {
	const data =
		file.type === 'office'
			? await officeBlob(file, chatId, 'file')
			: new Blob([(await getFileContentById(file.id)) as ArrayBuffer]);
	saveAs(data, file.name);
};

/** The file's type as its extension, e.g. PPTX. */
export const fileKind = (file: DeliveredFile): string =>
	(file.name.split('.').pop() ?? '').toUpperCase();
