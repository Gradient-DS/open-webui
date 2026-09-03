<!--
	Generative-UI `map` widget — an interactive Leaflet map of a Dutch
	address and its surroundings, rendered inline from an agent
	`present_ui` event (demo/kadaster branch).

	Props (MapProps) arrive validated from the agent service (assembled
	server-side by the kadaster `show_map` tool): a PDOK basemap, the target
	footprint + cadastral parcel, labelled markers, the surrounding BAG
	buildings (hover = facts, click = ask the agent about that building),
	an optional construction-year colouring with legend, and an optional
	aerial-photo year slider. Leaflet is lazy-imported so it stays
	code-split. All geometry is GeoJSON EPSG:4326 ([lon, lat]).
-->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { Map as LeafletMap, Layer, TileLayer } from 'leaflet';
	import type { GeoJsonObject } from 'geojson';
	import { submitPromptSignal } from '$lib/stores';
	import type { MapProps, MapBuilding } from '$lib/types/present_ui';

	// Typed props passed straight through by PresentUIDispatcher.
	export let props: MapProps;
	export let blockId: string = '';
	export let messageId: string = '';
	export let messageDone: boolean = false;

	const NL_CENTER: [number, number] = [5.387, 52.156];

	// PDOK EPSG:3857 WMTS tiles drop straight into a Leaflet XYZ layer.
	const LUCHTFOTO_BASE = 'https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0';
	const BASEMAPS: Record<string, { url: string; maxNativeZoom: number }> = {
		luchtfoto: {
			url: `${LUCHTFOTO_BASE}/Actueel_ortho25/EPSG:3857/{z}/{x}/{y}.jpeg`,
			maxNativeZoom: 19
		},
		brt: {
			url: 'https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/standaard/EPSG:3857/{z}/{x}/{y}.png',
			maxNativeZoom: 19
		}
	};
	// PDOK publishes one orthophoto layer per year next to "Actueel"
	// (GetCapabilities 2026-09-03): 2016–2020 and 2022–2025 have a 25 cm
	// layer, 2021 and 2026 only the HR (8 cm) one.
	const HR_ONLY_YEARS = new Set([2021, 2026]);
	const yearLayerUrl = (year: number) =>
		`${LUCHTFOTO_BASE}/${year}_${HR_ONLY_YEARS.has(year) ? 'orthoHR' : 'ortho25'}/EPSG:3857/{z}/{x}/{y}.jpeg`;

	// Construction-year colour ramp (oldest = warm, newest = cool).
	const YEAR_BINS: Array<{ max: number; color: string; label: string }> = [
		{ max: 1900, color: '#7f1d1d', label: '< 1900' },
		{ max: 1945, color: '#dc2626', label: '1900–1944' },
		{ max: 1970, color: '#f97316', label: '1945–1969' },
		{ max: 1990, color: '#eab308', label: '1970–1989' },
		{ max: 2010, color: '#22c55e', label: '1990–2009' },
		{ max: Infinity, color: '#2563eb', label: '≥ 2010' }
	];
	const UNKNOWN_COLOR = '#9ca3af';
	const yearColor = (year: number | null | undefined): string => {
		if (year == null || year <= 1000) return UNKNOWN_COLOR;
		return (YEAR_BINS.find((b) => year < b.max) ?? YEAR_BINS[YEAR_BINS.length - 1]).color;
	};

	let mapElement: HTMLDivElement;
	let map: LeafletMap | null = null;
	let aerialLayer: TileLayer | null = null;
	let L: typeof import('leaflet') | null = null;

	$: colorByYear = props.color_by === 'bouwjaar';
	$: years = (props.aerial_years ?? []).slice().sort((a, b) => a - b);
	$: hasTimeline = years.length > 0;
	let selectedYear: number | null = null;
	let yearIndex = 0;

	function buildingStyle(b: MapBuilding) {
		if (b.target) {
			return { color: '#1d4ed8', weight: 2, fillColor: '#3b82f6', fillOpacity: 0.45 };
		}
		if (colorByYear) {
			const c = yearColor(b.bouwjaar);
			return { color: c, weight: 1, fillColor: c, fillOpacity: 0.55 };
		}
		return { color: '#f8fafc', weight: 1, fillColor: '#e2e8f0', fillOpacity: 0.25 };
	}

	function buildingTooltip(b: MapBuilding): string {
		const parts: string[] = [];
		if (b.bouwjaar != null) parts.push(`built ${b.bouwjaar}`);
		if (b.gebruiksdoel) parts.push(b.gebruiksdoel);
		const facts = parts.length ? parts.join(' · ') : 'no BAG facts';
		const hint =
			props.clickable !== false ? '<br/><span class="opacity-70">click to ask</span>' : '';
		return `<b>${b.target ? 'This building' : 'Pand ' + b.pandid}</b><br/>${facts}${hint}`;
	}

	function askAbout(b: MapBuilding) {
		if (props.clickable === false || !messageDone) return;
		submitPromptSignal.set({
			text: `Tell me about the building with BAG pand id ${b.pandid}.`,
			ts: Date.now()
		});
	}

	function setYear(index: number) {
		if (!map || !L || !hasTimeline) return;
		yearIndex = index;
		selectedYear = years[index] ?? null;
		if (aerialLayer) {
			aerialLayer.setUrl(
				selectedYear === null ? BASEMAPS.luchtfoto.url : yearLayerUrl(selectedYear)
			);
		}
	}

	onMount(async () => {
		const [{ default: Leaflet }] = await Promise.all([
			import('leaflet'),
			import('leaflet/dist/leaflet.css')
		]);
		L = Leaflet;

		const center = props.center ?? NL_CENTER;
		const zoom = props.zoom ?? 18;
		const basemap = BASEMAPS[props.basemap ?? 'luchtfoto'] ?? BASEMAPS.luchtfoto;

		// Leaflet wants [lat, lon]; our props are GeoJSON [lon, lat].
		const view: [number, number] = [center[1], center[0]];
		map = L.map(mapElement).setView(view, zoom);

		aerialLayer = L.tileLayer(basemap.url, {
			maxNativeZoom: basemap.maxNativeZoom,
			maxZoom: 21,
			attribution: '© Kadaster / PDOK · BAG · CBS'
		}).addTo(map);
		if (hasTimeline) {
			// Start on the most recent year so the first drag goes back in time.
			setYear(years.length - 1);
		}

		const overlays: Layer[] = [];
		const targetOverlays: Layer[] = [];

		if (props.parcel) {
			const parcel = L.geoJSON(props.parcel as unknown as GeoJsonObject, {
				style: { color: '#f59e0b', weight: 2, fill: false, dashArray: '4 3' }
			}).addTo(map);
			overlays.push(parcel);
			targetOverlays.push(parcel);
		}

		for (const b of props.buildings ?? []) {
			const layer = L.geoJSON(b.geometry as unknown as GeoJsonObject, {
				style: buildingStyle(b)
			});
			layer.bindTooltip(buildingTooltip(b), { sticky: true, className: 'kadaster-tip' });
			layer.on('click', () => askAbout(b));
			layer.on('mouseover', () => layer.setStyle({ weight: 3 }));
			layer.on('mouseout', () => layer.setStyle(buildingStyle(b)));
			layer.addTo(map);
			overlays.push(layer);
			if (b.target) targetOverlays.push(layer);
		}

		// Explicit target footprint (drawn on top of the neighbours).
		if (props.footprint && !(props.buildings ?? []).some((b) => b.target)) {
			const fp = L.geoJSON(props.footprint as unknown as GeoJsonObject, {
				style: { color: '#1d4ed8', weight: 2, fillColor: '#3b82f6', fillOpacity: 0.45 }
			}).addTo(map);
			overlays.push(fp);
			targetOverlays.push(fp);
		}

		const markers = props.markers ?? [];
		for (const m of markers) {
			const pin = L.circleMarker([m.lat, m.lon], {
				radius: 7,
				color: '#ffffff',
				weight: 2,
				fillColor: '#dc2626',
				fillOpacity: 0.95
			}).addTo(map);
			if (m.label) {
				pin.bindTooltip(m.label, {
					permanent: markers.length > 1,
					direction: 'top',
					offset: [0, -8]
				});
			}
			overlays.push(pin);
			targetOverlays.push(pin);
		}

		// Frame: the whole fetched area when the question was about the
		// surroundings (heatmap / radius), else tight on the target.
		const frame =
			colorByYear || (props.radius_m ?? 0) > 0 || markers.length > 1 ? overlays : targetOverlays;
		if (frame.length) {
			try {
				map.fitBounds(L.featureGroup(frame).getBounds(), { maxZoom: 19, padding: [24, 24] });
			} catch {
				// Keep the initial centre/zoom if bounds can't be computed.
			}
		}

		// The map often mounts inside an animating/streaming container;
		// recompute tile layout once the layout settles.
		setTimeout(() => map && map.invalidateSize(), 0);
	});

	onDestroy(() => {
		if (map) {
			map.remove();
			map = null;
		}
	});
</script>

<div
	class="relative my-2 w-full overflow-hidden rounded-xl border border-gray-100 dark:border-gray-800"
	data-block-id={blockId}
	data-message-id={messageId}
	data-message-done={messageDone}
>
	<div bind:this={mapElement} class="h-96 w-full z-0"></div>

	{#if colorByYear}
		<div
			class="pointer-events-none absolute bottom-6 right-2 z-[1000] rounded-lg bg-white/90 px-2.5 py-2 text-[11px] leading-4 text-gray-800 shadow dark:bg-gray-900/90 dark:text-gray-100"
		>
			<div class="mb-1 font-medium">
				Construction year{#if props.radius_m}
					· {props.radius_m} m{/if}
			</div>
			{#each YEAR_BINS as bin}
				<div class="flex items-center gap-1.5">
					<span class="inline-block h-2.5 w-2.5 rounded-sm" style="background:{bin.color}"></span>
					<span>{bin.label}</span>
				</div>
			{/each}
			<div class="flex items-center gap-1.5">
				<span class="inline-block h-2.5 w-2.5 rounded-sm" style="background:{UNKNOWN_COLOR}"></span>
				<span>unknown</span>
			</div>
		</div>
	{/if}

	{#if hasTimeline}
		<div
			class="absolute left-2 top-2 z-[1000] flex items-center gap-2 rounded-lg bg-white/90 px-2.5 py-1.5 text-[11px] text-gray-800 shadow dark:bg-gray-900/90 dark:text-gray-100"
		>
			<span class="font-medium tabular-nums">Aerial {selectedYear ?? years[years.length - 1]}</span>
			<input
				type="range"
				class="w-40 accent-blue-600"
				min="0"
				max={years.length - 1}
				step="1"
				value={yearIndex}
				on:input={(e) => setYear(Number((e.currentTarget as HTMLInputElement).value))}
			/>
			<span class="opacity-70">{years[0]}–{years[years.length - 1]}</span>
		</div>
	{/if}

	{#if (props.buildings ?? []).length > 0 && props.clickable !== false}
		<div
			class="pointer-events-none absolute bottom-1 left-2 z-[1000] rounded bg-black/40 px-1.5 py-0.5 text-[10px] text-white"
		>
			Click a building to ask about it
		</div>
	{/if}
</div>

<style>
	:global(.kadaster-tip) {
		font-size: 11px;
		line-height: 1.3;
	}
</style>
