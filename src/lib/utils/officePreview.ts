/**
 * Shared Office (DOCX/XLSX) → HTML preview rendering.
 *
 * Isolates the heavy dynamic imports (mammoth, xlsx, dompurify) and the
 * sanitized-HTML generation that FileNav and FileItemModal both need, so the
 * conversion logic has a single authoritative representation.
 *
 * XLSX grid markup is delegated to excelToTable, which produces the
 * excel-col-hdr / excel-row-num / excel-num classes the office-preview CSS
 * relies on.
 */

import type { WorkBook } from 'xlsx';

/** Convert a DOCX array buffer to sanitized HTML. */
export const renderDocxHtml = async (arrayBuffer: ArrayBuffer): Promise<string> => {
	const mammoth = await import('mammoth');
	const result = await mammoth.convertToHtml({ arrayBuffer });
	const DOMPurify = (await import('dompurify')).default;
	return DOMPurify.sanitize(result.value);
};

/** Parse an array buffer into an XLSX workbook. */
export const readWorkbook = async (arrayBuffer: ArrayBuffer): Promise<WorkBook> => {
	const XLSX = await import('xlsx');
	return XLSX.read(new Uint8Array(arrayBuffer), { type: 'array' });
};

export interface SheetRender {
	html: string;
	rowCount: number;
}

/** Render a single workbook sheet as sanitized table HTML. */
export const renderSheetHtml = async (wb: WorkBook, sheet: string): Promise<SheetRender> => {
	const { excelToTable } = await import('$lib/utils/excelToTable');
	const result = await excelToTable(wb.Sheets[sheet]);
	return { html: result.html, rowCount: result.rowCount };
};
