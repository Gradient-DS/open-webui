// [Gradient] A delivered CSV as rows for the document panel's table preview.

export type CsvTable = { header: string[]; rows: string[][]; truncated: boolean };

/** The separator a CSV uses: a semicolon when its first line has more of them than commas. */
export const csvDelimiter = (text: string): ',' | ';' => {
	const first = text.split('\n', 1)[0] ?? '';
	return (first.match(/;/g) ?? []).length > (first.match(/,/g) ?? []).length ? ';' : ',';
};

/** The first `maxRows` rows of a CSV after its header; quoted fields may hold separators,
 * doubled quotes and line breaks. */
export const parseCsv = (text: string, maxRows = 200): CsvTable => {
	const delimiter = csvDelimiter(text);
	const records: string[][] = [];
	let record: string[] = [];
	let field = '';
	let quoted = false;
	let i = 0;
	const source = text.replace(/^\uFEFF/, '');
	while (i < source.length && records.length <= maxRows + 1) {
		const char = source[i];
		if (quoted) {
			if (char === '"' && source[i + 1] === '"') {
				field += '"';
				i += 1;
			} else if (char === '"') {
				quoted = false;
			} else {
				field += char;
			}
		} else if (char === '"' && field === '') {
			quoted = true;
		} else if (char === delimiter) {
			record.push(field);
			field = '';
		} else if (char === '\n' || char === '\r') {
			if (char === '\r' && source[i + 1] === '\n') i += 1;
			record.push(field);
			records.push(record);
			record = [];
			field = '';
		} else {
			field += char;
		}
		i += 1;
	}
	if (field !== '' || record.length > 0) {
		record.push(field);
		records.push(record);
	}
	const [header = [], ...rows] = records;
	return {
		header,
		rows: rows.slice(0, maxRows),
		truncated: rows.length > maxRows || i < source.length
	};
};
