// [Gradient] pdfmake's standard-font containers ship without types; documentPdfMarkdown registers Courier.
declare module 'pdfmake/build/standard-fonts/Courier' {
	import type { TFontContainer } from 'pdfmake/build/pdfmake';
	const fontContainer: TFontContainer;
	export default fontContainer;
}
