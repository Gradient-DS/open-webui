// [Gradient] An Office file an agent delivered: a stored file plus the ids of its rendered pages, shown in the
// document panel next to generated documents.
import fileSaver from 'file-saver';

import { getFileContentById } from '$lib/apis/files';

const { saveAs } = fileSaver;

export type DeliveredFile = { id: string; name: string; url: string; pages: string[] };

/** A message file the agent delivered with rendered pages. */
export const isDeliveredFile = (file: any): file is DeliveredFile =>
	file?.type === 'file' &&
	typeof file?.id === 'string' &&
	Array.isArray(file?.pages) &&
	file.pages.length > 0;

/** Each page as an object URL, loaded with the user's token; the caller revokes them. Throws when one fails. */
export const loadPages = async (file: DeliveredFile): Promise<string[]> => {
	const pages = await Promise.all(file.pages.map((id) => getFileContentById(id)));
	return pages.map((data) =>
		URL.createObjectURL(new Blob([data as ArrayBuffer], { type: 'image/png' }))
	);
};

/** Throws when the file cannot be read. */
export const downloadDeliveredFile = async (file: DeliveredFile): Promise<void> => {
	const data = await getFileContentById(file.id);
	saveAs(new Blob([data as ArrayBuffer]), file.name);
};

/** The file's type as its extension, e.g. PPTX. */
export const fileKind = (file: DeliveredFile): string =>
	(file.name.split('.').pop() ?? '').toUpperCase();
