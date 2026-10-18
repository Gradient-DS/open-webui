// [Gradient] Fades the edge of a horizontal scroller where more content is hidden,
// so a clipped chip reads as "scroll for more" instead of a broken layout.

/** The CSS mask for a scroller, given how far it is scrolled and how much is hidden. */
export const fadeMask = (scrollLeft: number, scrollWidth: number, clientWidth: number): string => {
	const left = scrollLeft > 1;
	const right = scrollLeft + clientWidth < scrollWidth - 1;
	if (!left && !right) return '';
	const start = left ? 'transparent, black 1.5rem' : 'black';
	const end = right ? 'black calc(100% - 1.5rem), transparent' : 'black';
	return `linear-gradient(to right, ${start}, ${end})`;
};

export const overflowFade = (node: HTMLElement) => {
	const update = () => {
		const mask = fadeMask(node.scrollLeft, node.scrollWidth, node.clientWidth);
		node.style.maskImage = mask;
		node.style.webkitMaskImage = mask;
	};
	const resize = new ResizeObserver(update);
	resize.observe(node);
	const children = new MutationObserver(update);
	children.observe(node, { childList: true, subtree: true });
	node.addEventListener('scroll', update, { passive: true });
	update();
	return {
		destroy() {
			resize.disconnect();
			children.disconnect();
			node.removeEventListener('scroll', update);
		}
	};
};
