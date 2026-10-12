/** window.open features for a width x height window centered over the current browser window. */
export function centeredPopupFeatures(width: number, height: number): string {
	const left = Math.round(window.screenX + (window.outerWidth - width) / 2);
	const top = Math.round(window.screenY + (window.outerHeight - height) / 2);
	return `width=${width},height=${height},left=${left},top=${top}`;
}
