/**
 * [Gradient] What a slide takes from its layout and master, for the PPTX preview.
 *
 * A slide stores only what its author changed. The graphics around it, the text styles of its
 * placeholders and, in a template (.potx), the prompts of the placeholders nobody filled in yet all
 * live in the layout and the master. Without them a template previews as near-empty slides.
 */

const EMU_PER_PX = 9525;
const A_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main';
const P_NS = 'http://schemas.openxmlformats.org/presentationml/2006/main';
const FILL_TAGS = ['noFill', 'solidFill', 'gradFill', 'blipFill', 'pattFill', 'grpFill'];
const BULLET_TAGS = ['buNone', 'buChar', 'buAutoNum'];
const BODY_ATTRIBUTES = ['anchor', 'lIns', 'tIns', 'rIns', 'bIns'];
/** Placeholders that inherit from the master placeholder of their own type. */
const FIELD_TYPES = ['dt', 'ftr', 'sldNum'];

type Rect = { x: number; y: number; w: number; h: number };
type Relationships = Record<string, { type: string; target: string }>;
type ZipLike = { file: (path: string) => { async: (type: 'text') => Promise<string> } | null };

export type InheritedPart = { doc: Document | null; rels: Relationships };

export type InheritTools = {
	/** Package path → data URI of every picture in the file. */
	media: Record<string, string>;
	colorOf: (el: Element | undefined) => string | null;
	loadImage: (src: string) => Promise<HTMLImageElement>;
	drawText: (ctx: CanvasRenderingContext2D, txBody: Element, rect: Rect) => void;
};

const tagOf = (el: Element) => el.tagName.split(':').pop() ?? '';
const child = (el: Element | null | undefined, tag: string): Element | undefined =>
	el ? Array.from(el.children).find((candidate) => tagOf(candidate) === tag) : undefined;
const childOf = (el: Element | null | undefined, tags: string[]): Element | undefined =>
	el ? Array.from(el.children).find((candidate) => tags.includes(tagOf(candidate))) : undefined;
const px = (el: Element | undefined, attribute: string) =>
	(parseInt(el?.getAttribute(attribute) ?? '0', 10) || 0) / EMU_PER_PX;

// ── Placeholders: prompts, text styles and fills ───────────────────────────

type PlaceholderShape = { shape: Element; type: string; idx: string; customPrompt: boolean };

const placeholderOf = (shape: Element): Omit<PlaceholderShape, 'shape'> | null => {
	const ph = child(child(childOf(shape, ['nvSpPr', 'nvPicPr']), 'nvPr'), 'ph');
	if (!ph) return null;
	return {
		type: ph.getAttribute('type') ?? 'obj',
		idx: ph.getAttribute('idx') ?? '',
		customPrompt: ph.getAttribute('hasCustomPrompt') === '1'
	};
};

const placeholderShapes = (doc: Document | null): PlaceholderShape[] =>
	Array.from(doc?.getElementsByTagName('p:sp') ?? []).flatMap((shape) => {
		const ph = placeholderOf(shape);
		return ph ? [{ shape, ...ph }] : [];
	});

/** The master placeholder a type inherits from: every content type follows the body. */
const masterType = (type: string) =>
	type === 'ctrTitle' || type === 'title' ? 'title' : FIELD_TYPES.includes(type) ? type : 'body';

const layoutMatch = (ph: PlaceholderShape, layout: PlaceholderShape[]) =>
	(ph.idx ? layout.find((candidate) => candidate.idx === ph.idx) : undefined) ??
	layout.find((candidate) => candidate.type === ph.type) ??
	layout.find((candidate) => masterType(candidate.type) === masterType(ph.type) && !ph.idx);

const hasText = (txBody: Element | undefined) =>
	Array.from(txBody?.getElementsByTagName('a:t') ?? []).some((text) => text.textContent?.trim()) ||
	(txBody?.getElementsByTagName('a:fld').length ?? 0) > 0;

type LevelStyle = {
	algn: string | null;
	runAttributes: Record<string, string>;
	fill: Element | undefined;
	latin: Element | undefined;
	bullet: Element | undefined;
};

/** The first value each list-style source gives for a paragraph level; sources go nearest first. */
const levelStyle = (sources: Array<Element | undefined>, level: number): LevelStyle => {
	const levels = sources
		.map((source) => child(source, `lvl${level + 1}pPr`))
		.filter((el): el is Element => Boolean(el));
	const runs = levels.map((el) => child(el, 'defRPr')).filter((el): el is Element => Boolean(el));
	const runAttributes: Record<string, string> = {};
	for (const attribute of ['sz', 'b', 'i']) {
		const value = runs.find((run) => run.hasAttribute(attribute))?.getAttribute(attribute);
		if (value) runAttributes[attribute] = value;
	}
	return {
		algn: levels.find((el) => el.hasAttribute('algn'))?.getAttribute('algn') ?? null,
		runAttributes,
		fill: runs.map((run) => child(run, 'solidFill')).find(Boolean),
		latin: runs.map((run) => child(run, 'latin')).find(Boolean),
		bullet: levels.map((el) => childOf(el, BULLET_TAGS)).find(Boolean)
	};
};

/** Writes a level's inherited style onto the paragraph, where the renderer reads it. */
const applyLevelStyle = (paragraph: Element, style: LevelStyle) => {
	const doc = paragraph.ownerDocument;
	let pPr = child(paragraph, 'pPr');
	if (!pPr) {
		pPr = doc.createElementNS(A_NS, 'a:pPr');
		paragraph.insertBefore(pPr, paragraph.firstChild);
	}
	if (style.algn && !pPr.hasAttribute('algn')) pPr.setAttribute('algn', style.algn);
	if (style.bullet && !childOf(pPr, BULLET_TAGS)) pPr.appendChild(style.bullet.cloneNode(true));

	let defRPr = child(pPr, 'defRPr');
	if (!defRPr) {
		defRPr = doc.createElementNS(A_NS, 'a:defRPr');
		pPr.appendChild(defRPr);
	}
	for (const [attribute, value] of Object.entries(style.runAttributes)) {
		if (!defRPr.hasAttribute(attribute)) defRPr.setAttribute(attribute, value);
	}
	if (style.fill && !child(defRPr, 'solidFill')) defRPr.appendChild(style.fill.cloneNode(true));
	if (style.latin && !child(defRPr, 'latin')) defRPr.appendChild(style.latin.cloneNode(true));
};

/**
 * Gives every placeholder of a slide what it inherits: the text style, anchoring, insets and fill
 * of its layout and master placeholder and, with `showPrompts`, the prompt the layout's author wrote
 * for it when it is empty (the stock "Click to add text" is PowerPoint's own and is not shown).
 * Changes `slideDoc` in place; nothing the slide sets itself is overwritten.
 */
export const inheritPlaceholders = (
	slideDoc: Document,
	layoutDoc: Document | null,
	masterDoc: Document | null,
	showPrompts: boolean
) => {
	const layoutShapes = placeholderShapes(layoutDoc);
	const masterShapes = placeholderShapes(masterDoc);
	const textStyles = masterDoc?.getElementsByTagName('p:txStyles')[0];

	for (const ph of placeholderShapes(slideDoc)) {
		const inherited = masterType(ph.type);
		const layoutShape = layoutMatch(ph, layoutShapes);
		const sources = [
			layoutShape?.shape,
			masterShapes.find((candidate) => masterType(candidate.type) === inherited)?.shape
		].filter((shape): shape is Element => Boolean(shape));
		const sourceBodies = sources.map((shape) => child(shape, 'txBody'));

		const spPr = child(ph.shape, 'spPr');
		const fill = sources.map((shape) => childOf(child(shape, 'spPr'), FILL_TAGS)).find(Boolean);
		if (spPr && fill && !childOf(spPr, FILL_TAGS)) spPr.appendChild(fill.cloneNode(true));

		let txBody = child(ph.shape, 'txBody');
		const prompt = layoutShape?.customPrompt ? child(layoutShape.shape, 'txBody') : undefined;
		if (showPrompts && !hasText(txBody) && hasText(prompt)) {
			if (!txBody) {
				txBody = slideDoc.createElementNS(P_NS, 'p:txBody');
				txBody.appendChild(slideDoc.createElementNS(A_NS, 'a:bodyPr'));
				ph.shape.appendChild(txBody);
			}
			for (const paragraph of Array.from(txBody.children)) {
				if (tagOf(paragraph) === 'p') paragraph.remove();
			}
			for (const paragraph of Array.from(prompt?.children ?? [])) {
				if (tagOf(paragraph) === 'p') txBody.appendChild(paragraph.cloneNode(true));
			}
		}
		if (!txBody) continue;

		const bodyPr = child(txBody, 'bodyPr');
		for (const attribute of BODY_ATTRIBUTES) {
			const value = sourceBodies
				.map((body) => child(body, 'bodyPr'))
				.find((el) => el?.hasAttribute(attribute))
				?.getAttribute(attribute);
			if (bodyPr && value && !bodyPr.hasAttribute(attribute)) bodyPr.setAttribute(attribute, value);
		}

		const listStyles = [
			child(txBody, 'lstStyle'),
			...sourceBodies.map((body) => child(body, 'lstStyle')),
			child(
				textStyles,
				inherited === 'title' ? 'titleStyle' : inherited === 'body' ? 'bodyStyle' : 'otherStyle'
			)
		];
		for (const paragraph of Array.from(txBody.children)) {
			if (tagOf(paragraph) !== 'p') continue;
			const level = parseInt(child(paragraph, 'pPr')?.getAttribute('lvl') ?? '0', 10) || 0;
			applyLevelStyle(paragraph, levelStyle(listStyles, level));
		}
	}
};

// ── Graphics: the shapes and pictures of the layout and master ─────────────

type Box = Rect & { rotation: number; flipH: boolean; flipV: boolean };

const boxOf = (properties: Element | undefined): { box: Box; xfrm: Element } | null => {
	const xfrm = child(properties, 'xfrm');
	const off = child(xfrm, 'off');
	const ext = child(xfrm, 'ext');
	if (!xfrm || !off || !ext) return null;
	return {
		xfrm,
		box: {
			x: px(off, 'x'),
			y: px(off, 'y'),
			w: px(ext, 'cx'),
			h: px(ext, 'cy'),
			rotation: ((parseInt(xfrm.getAttribute('rot') ?? '0', 10) || 0) / 60000) * (Math.PI / 180),
			flipH: xfrm.getAttribute('flipH') === '1',
			flipV: xfrm.getAttribute('flipV') === '1'
		}
	};
};

/** Moves the origin to the box's top-left corner, rotated and mirrored as the shape is. */
const enterBox = (ctx: CanvasRenderingContext2D, box: Box) => {
	ctx.translate(box.x + box.w / 2, box.y + box.h / 2);
	if (box.rotation) ctx.rotate(box.rotation);
	if (box.flipH || box.flipV) ctx.scale(box.flipH ? -1 : 1, box.flipV ? -1 : 1);
	ctx.translate(-box.w / 2, -box.h / 2);
};

const point = (el: Element, sx: number, sy: number): [number, number] => [
	Number(el.getAttribute('x')) * sx,
	Number(el.getAttribute('y')) * sy
];

/** A custom geometry as a canvas path, or null when it uses formulas the preview cannot evaluate. */
const customPath = (geometry: Element, w: number, h: number): Path2D | null => {
	const path = new Path2D();
	for (const source of Array.from(child(geometry, 'pathLst')?.children ?? [])) {
		if (source.getAttribute('fill') === 'none') continue;
		const sx = w / (Number(source.getAttribute('w')) || w || 1);
		const sy = h / (Number(source.getAttribute('h')) || h || 1);
		let current: [number, number] = [0, 0];
		for (const command of Array.from(source.children)) {
			const points = Array.from(command.children).map((pt) => point(pt, sx, sy));
			if (points.flat().some(Number.isNaN)) return null;
			switch (tagOf(command)) {
				case 'moveTo':
					path.moveTo(...points[0]);
					break;
				case 'lnTo':
					path.lineTo(...points[0]);
					break;
				case 'cubicBezTo':
					if (points.length < 3) return null;
					path.bezierCurveTo(...points[0], ...points[1], ...points[2]);
					break;
				case 'quadBezTo':
					if (points.length < 2) return null;
					path.quadraticCurveTo(...points[0], ...points[1]);
					break;
				case 'arcTo': {
					const rx = Number(command.getAttribute('wR')) * sx;
					const ry = Number(command.getAttribute('hR')) * sy;
					const start = (Number(command.getAttribute('stAng')) / 60000) * (Math.PI / 180);
					const sweep = (Number(command.getAttribute('swAng')) / 60000) * (Math.PI / 180);
					if ([rx, ry, start, sweep].some(Number.isNaN)) return null;
					const cx = current[0] - rx * Math.cos(start);
					const cy = current[1] - ry * Math.sin(start);
					path.ellipse(cx, cy, rx, ry, 0, start, start + sweep, sweep < 0);
					points.push([cx + rx * Math.cos(start + sweep), cy + ry * Math.sin(start + sweep)]);
					break;
				}
				case 'close':
					path.closePath();
					break;
			}
			if (points.length > 0) current = points[points.length - 1];
		}
	}
	return path;
};

/** The outline of a shape in its own box, or null for a preset the preview does not know. */
const shapePath = (spPr: Element | undefined, w: number, h: number): Path2D | null => {
	const custom = child(spPr, 'custGeom');
	if (custom) return customPath(custom, w, h);

	const path = new Path2D();
	switch (child(spPr, 'prstGeom')?.getAttribute('prst') ?? 'rect') {
		case 'rect':
			path.rect(0, 0, w, h);
			return path;
		case 'ellipse':
			path.ellipse(w / 2, h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
			return path;
		case 'roundRect':
			path.roundRect(0, 0, w, h, Math.min(w, h) / 6);
			return path;
		case 'triangle':
			path.moveTo(w / 2, 0);
			path.lineTo(w, h);
			path.lineTo(0, h);
			path.closePath();
			return path;
		case 'rtTriangle':
			path.moveTo(0, 0);
			path.lineTo(w, h);
			path.lineTo(0, h);
			path.closePath();
			return path;
		default:
			return null;
	}
};

const imageCache = new WeakMap<object, Map<string, Promise<HTMLImageElement | null>>>();

const pictureOf = (blipFill: Element | undefined, part: InheritedPart, tools: InheritTools) => {
	const embed = child(blipFill, 'blip')?.getAttribute('r:embed') ?? '';
	const uri = tools.media[part.rels[embed]?.target ?? ''];
	if (!uri || uri.includes('image/x-emf')) return null;
	const cache = imageCache.get(tools.media) ?? new Map();
	imageCache.set(tools.media, cache);
	if (!cache.has(uri))
		cache.set(
			uri,
			tools.loadImage(uri).catch(() => null)
		);
	return cache.get(uri) ?? null;
};

const drawPicture = async (
	ctx: CanvasRenderingContext2D,
	blipFill: Element | undefined,
	box: Box,
	part: InheritedPart,
	tools: InheritTools,
	clip?: Path2D
) => {
	const image = await pictureOf(blipFill, part, tools);
	if (!image) return;
	const crop = child(blipFill, 'srcRect');
	const share = (side: string) => (parseInt(crop?.getAttribute(side) ?? '0', 10) || 0) / 100000;
	const sx = image.naturalWidth * share('l');
	const sy = image.naturalHeight * share('t');
	const sw = image.naturalWidth * (1 - share('l') - share('r'));
	const sh = image.naturalHeight * (1 - share('t') - share('b'));
	if (sw <= 0 || sh <= 0) return;
	ctx.save();
	enterBox(ctx, box);
	if (clip) ctx.clip(clip);
	ctx.drawImage(image, sx, sy, sw, sh, 0, 0, box.w, box.h);
	ctx.restore();
};

const fillStyleOf = (
	ctx: CanvasRenderingContext2D,
	fill: Element,
	box: Box,
	tools: InheritTools
): string | CanvasGradient | null => {
	if (tagOf(fill) === 'solidFill') return tools.colorOf(fill);
	if (tagOf(fill) !== 'gradFill') return null;
	const stops = Array.from(fill.getElementsByTagName('a:gs')).flatMap((stop) => {
		const color = tools.colorOf(stop);
		const position = (parseInt(stop.getAttribute('pos') ?? '0', 10) || 0) / 100000;
		return color ? [{ color, position: Math.max(0, Math.min(1, position)) }] : [];
	});
	if (stops.length === 0) return null;
	const angle =
		((parseInt(child(fill, 'lin')?.getAttribute('ang') ?? '0', 10) || 0) / 60000) * (Math.PI / 180);
	const dx = (Math.cos(angle) * box.w) / 2;
	const dy = (Math.sin(angle) * box.h) / 2;
	const gradient = ctx.createLinearGradient(
		box.w / 2 - dx,
		box.h / 2 - dy,
		box.w / 2 + dx,
		box.h / 2 + dy
	);
	for (const stop of stops) gradient.addColorStop(stop.position, stop.color);
	return gradient;
};

const drawShape = async (
	ctx: CanvasRenderingContext2D,
	shape: Element,
	part: InheritedPart,
	tools: InheritTools
) => {
	const spPr = child(shape, 'spPr');
	const placed = boxOf(spPr);
	if (!placed || (placed.box.w === 0 && placed.box.h === 0)) return;
	const { box } = placed;
	const style = child(shape, 'style');
	const connector = tagOf(shape) === 'cxnSp';
	const outline = connector ? null : shapePath(spPr, box.w, box.h);

	const fill = childOf(spPr, FILL_TAGS);
	if (outline && fill && tagOf(fill) === 'blipFill') {
		await drawPicture(ctx, fill, box, part, tools, outline);
	} else if (outline) {
		const reference = child(style, 'fillRef');
		const paint = fill
			? fillStyleOf(ctx, fill, box, tools)
			: reference && reference.getAttribute('idx') !== '0'
				? tools.colorOf(reference)
				: null;
		if (paint) {
			ctx.save();
			enterBox(ctx, box);
			ctx.fillStyle = paint;
			ctx.fill(outline);
			ctx.restore();
		}
	}

	const line = child(spPr, 'ln');
	const lineReference = child(style, 'lnRef');
	const stroke = child(line, 'noFill')
		? null
		: child(line, 'solidFill')
			? tools.colorOf(child(line, 'solidFill'))
			: lineReference && lineReference.getAttribute('idx') !== '0'
				? tools.colorOf(lineReference)
				: null;
	if (stroke && (outline || connector)) {
		ctx.save();
		enterBox(ctx, box);
		ctx.strokeStyle = stroke;
		ctx.lineWidth = Math.max(0.75, px(line, 'w') || 1);
		if (outline) {
			ctx.stroke(outline);
		} else {
			ctx.beginPath();
			ctx.moveTo(0, 0);
			ctx.lineTo(box.w, box.h);
			ctx.stroke();
		}
		ctx.restore();
	}

	const txBody = child(shape, 'txBody');
	if (txBody && hasText(txBody)) tools.drawText(ctx, txBody, box);
};

const drawTree = async (
	ctx: CanvasRenderingContext2D,
	tree: Element,
	part: InheritedPart,
	tools: InheritTools
) => {
	for (const el of Array.from(tree.children)) {
		const tag = tagOf(el);
		if (tag === 'grpSp') {
			const placed = boxOf(child(el, 'grpSpPr'));
			const childOff = child(placed?.xfrm, 'chOff');
			const childExt = child(placed?.xfrm, 'chExt');
			ctx.save();
			if (placed && px(childExt, 'cx') > 0 && px(childExt, 'cy') > 0) {
				enterBox(ctx, placed.box);
				ctx.scale(placed.box.w / px(childExt, 'cx'), placed.box.h / px(childExt, 'cy'));
				ctx.translate(-px(childOff, 'x'), -px(childOff, 'y'));
			}
			await drawTree(ctx, el, part, tools);
			ctx.restore();
		} else if ((tag === 'sp' || tag === 'cxnSp') && !placeholderOf(el)) {
			await drawShape(ctx, el, part, tools);
		} else if (tag === 'pic' && !placeholderOf(el)) {
			const placed = boxOf(child(el, 'spPr'));
			if (placed) await drawPicture(ctx, child(el, 'blipFill'), placed.box, part, tools);
		}
	}
};

/**
 * Rewrites mapped colour names (tx1, bg1, …) to the theme colours the slide's colour map gives them:
 * a dark layout swaps text and background, which the renderer's fixed mapping does not know.
 */
const applyColourMap = (docs: Array<Document | null>) => {
	const [slide, layout, master] = docs;
	const map =
		slide?.getElementsByTagName('a:overrideClrMapping')[0] ??
		layout?.getElementsByTagName('a:overrideClrMapping')[0] ??
		master?.getElementsByTagName('p:clrMap')[0];
	if (!map) return;
	for (const doc of docs) {
		for (const colour of Array.from(doc?.getElementsByTagName('a:schemeClr') ?? [])) {
			const mapped = map.getAttribute(colour.getAttribute('val') ?? '');
			if (mapped) colour.setAttribute('val', mapped);
		}
	}
};

const showsMasterShapes = (doc: Document) =>
	doc.documentElement.getAttribute('showMasterSp') !== '0';

const templates = new WeakMap<object, Promise<boolean>>();

/** Whether the package is a template, which opens with the prompts of its empty placeholders. */
const isTemplate = (zip: ZipLike) => {
	if (!templates.has(zip)) {
		templates.set(
			zip,
			(async () =>
				((await zip.file('[Content_Types].xml')?.async('text')) ?? '').includes(
					'presentationml.template.main+xml'
				))()
		);
	}
	return templates.get(zip) as Promise<boolean>;
};

/**
 * Draws the graphics a slide shows from its master and layout, then completes the slide's own
 * placeholders (see `inheritPlaceholders`). Call it after the backgrounds, before the slide's shapes.
 */
export const inheritFromLayout = async (
	ctx: CanvasRenderingContext2D,
	zip: ZipLike,
	slideDoc: Document,
	layout: InheritedPart,
	master: InheritedPart,
	tools: InheritTools
) => {
	applyColourMap([slideDoc, layout.doc, master.doc]);
	if (showsMasterShapes(slideDoc)) {
		const parts = layout.doc && !showsMasterShapes(layout.doc) ? [layout] : [master, layout];
		for (const part of parts) {
			const tree = part.doc?.getElementsByTagName('p:spTree')[0];
			if (tree) await drawTree(ctx, tree, part, tools);
		}
	}
	inheritPlaceholders(slideDoc, layout.doc, master.doc, await isTemplate(zip));
};
