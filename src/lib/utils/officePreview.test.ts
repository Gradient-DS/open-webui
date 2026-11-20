import { describe, expect, it } from 'vitest';

import { officeKind } from './officePreview';

describe('officeKind', () => {
	it('gives a template the viewer of its document', () => {
		expect(officeKind('House style.potx')).toBe('pptx');
		expect(officeKind('Letter.DOTX')).toBe('docx');
		expect(officeKind('Budget.xltx')).toBe('xlsx');
		expect(officeKind('Deck.pptx')).toBe('pptx');
	});
	it('goes by content type when the name has no known extension', () => {
		expect(
			officeKind(
				'download',
				'application/vnd.openxmlformats-officedocument.presentationml.template; charset=binary'
			)
		).toBe('pptx');
		expect(officeKind('report.docx', 'application/octet-stream')).toBe('docx');
	});
	it('has no viewer for other files', () => {
		expect(officeKind('notes.pdf', 'application/pdf')).toBeNull();
		expect(officeKind('potx')).toBeNull();
		expect(officeKind(undefined)).toBeNull();
	});
});
