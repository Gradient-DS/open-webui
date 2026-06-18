<script lang="ts">
	import type { FlagCode } from '$lib/utils/models/profile';

	export let origin: FlagCode = 'EU';
	export let className = 'w-[18px] h-[13px]';
	export let ariaLabel = '';

	// viewBox is 60x40 (3:2). Stable id so the rounded-corner clip is unique per flag.
	const clipId = `flag-clip-${origin}`;

	// Build the points string for a 5-point star centred at (cx,cy) with outer
	// radius R, optionally rotated (deg). Inner radius is the canonical 0.382·R.
	const star = (cx: number, cy: number, R: number, rot = 0): string => {
		const pts: string[] = [];
		for (let k = 0; k < 10; k++) {
			const r = k % 2 === 0 ? R : R * 0.382;
			const a = ((-90 + k * 36 + rot) * Math.PI) / 180;
			pts.push(`${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`);
		}
		return pts.join(' ');
	};

	// EU: 12 gold stars evenly spaced on a ring.
	const euStars = Array.from({ length: 12 }, (_, i) => {
		const a = (i * 30 * Math.PI) / 180;
		return { cx: 30 + 13 * Math.sin(a), cy: 20 - 13 * Math.cos(a) };
	});

	// US: 6 white stripes (over a red field) + blue canton with a dot grid for stars.
	const stripeH = 40 / 13;
	const whiteStripes = [1, 3, 5, 7, 9, 11];
	const cantonW = 24;
	const cantonH = stripeH * 7;
	const usDots: { cx: number; cy: number }[] = [];
	for (let r = 0; r < 4; r++) {
		for (let c = 0; c < 5; c++) {
			usDots.push({
				cx: 3 + c * ((cantonW - 6) / 4),
				cy: 3.5 + r * ((cantonH - 7) / 3)
			});
		}
	}

	// CN: one large star with four small stars whose top point faces the big star.
	const cnBig = { cx: 11, cy: 11 };
	const cnSmall = [
		{ cx: 22, cy: 4 },
		{ cx: 26, cy: 9 },
		{ cx: 26, cy: 16 },
		{ cx: 22, cy: 21 }
	].map((s) => ({
		...s,
		rot: (Math.atan2(cnBig.cy - s.cy, cnBig.cx - s.cx) * 180) / Math.PI + 90
	}));
</script>

<svg
	xmlns="http://www.w3.org/2000/svg"
	viewBox="0 0 60 40"
	class={className}
	role="img"
	aria-label={ariaLabel}
>
	<defs>
		<clipPath id={clipId}>
			<rect x="0" y="0" width="60" height="40" rx="4" />
		</clipPath>
	</defs>

	<g clip-path={`url(#${clipId})`}>
		{#if origin === 'EU'}
			<rect width="60" height="40" fill="#003399" />
			{#each euStars as s}
				<polygon points={star(s.cx, s.cy, 2.2)} fill="#FFCC00" />
			{/each}
		{:else if origin === 'US'}
			<rect width="60" height="40" fill="#B22234" />
			{#each whiteStripes as s}
				<rect x="0" y={s * stripeH} width="60" height={stripeH} fill="#FFFFFF" />
			{/each}
			<rect x="0" y="0" width={cantonW} height={cantonH} fill="#3C3B6E" />
			{#each usDots as d}
				<circle cx={d.cx} cy={d.cy} r="0.9" fill="#FFFFFF" />
			{/each}
		{:else if origin === 'CN'}
			<rect width="60" height="40" fill="#DE2910" />
			<polygon points={star(cnBig.cx, cnBig.cy, 6)} fill="#FFDE00" />
			{#each cnSmall as s}
				<polygon points={star(s.cx, s.cy, 2.4, s.rot)} fill="#FFDE00" />
			{/each}
		{:else if origin === 'NL'}
			<!-- Three equal horizontal bands: red, white, blue -->
			<rect x="0" y="0" width="60" height="13.34" fill="#AE1C28" />
			<rect x="0" y="13.34" width="60" height="13.33" fill="#FFFFFF" />
			<rect x="0" y="26.67" width="60" height="13.33" fill="#21468B" />
		{/if}
	</g>
	<rect
		x="0.5"
		y="0.5"
		width="59"
		height="39"
		rx="3.5"
		fill="none"
		stroke="#000000"
		stroke-opacity="0.1"
	/>
</svg>
