<!--
	Generative-UI `map` widget — an interactive Leaflet map of a Dutch
	address rendered inline from an agent `present_ui` event.

	Props (MapProps) arrive validated from the agent service (assembled
	server-side by the kadaster `show_map` tool): a PDOK basemap, the
	building footprint + cadastral parcel as GeoJSON (EPSG:4326, [lon,lat]),
	and an address marker. Leaflet is lazy-imported so it stays code-split.
-->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { Map as LeafletMap, Layer } from 'leaflet';
	import type { GeoJsonObject } from 'geojson';
	import type { MapProps } from '$lib/types/present_ui';

	// Typed props passed straight through by PresentUIDispatcher.
	// blockId/messageId/messageDone are part of the widget contract;
	// surfaced as DOM data attributes for identity/testing.
	export let props: MapProps;
	export let blockId: string = '';
	export let messageId: string = '';
	export let messageDone: boolean = false;

	const NL_CENTER: [number, number] = [5.387, 52.156];

	// PDOK EPSG:3857 WMTS tiles drop straight into a Leaflet XYZ layer.
	const BASEMAPS: Record<string, { url: string; maxNativeZoom: number }> = {
		luchtfoto: {
			url: 'https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/Actueel_ortho25/EPSG:3857/{z}/{x}/{y}.jpeg',
			maxNativeZoom: 19
		},
		brt: {
			url: 'https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/standaard/EPSG:3857/{z}/{x}/{y}.png',
			maxNativeZoom: 19
		}
	};

	let mapElement: HTMLDivElement;
	let map: LeafletMap | null = null;

	onMount(async () => {
		const [{ default: L }] = await Promise.all([
			import('leaflet'),
			import('leaflet/dist/leaflet.css')
		]);

		const center = props.center ?? NL_CENTER;
		const zoom = props.zoom ?? 18;
		const basemap = BASEMAPS[props.basemap ?? 'luchtfoto'] ?? BASEMAPS.luchtfoto;

		// Leaflet wants [lat, lon]; our props are GeoJSON [lon, lat].
		const view: [number, number] = [center[1], center[0]];
		map = L.map(mapElement).setView(view, zoom);

		L.tileLayer(basemap.url, {
			maxNativeZoom: basemap.maxNativeZoom,
			maxZoom: 21,
			attribution: '© Kadaster / PDOK'
		}).addTo(map);

		const overlays: Layer[] = [];

		if (props.parcel) {
			overlays.push(
				L.geoJSON(props.parcel as unknown as GeoJsonObject, {
					style: { color: '#f59e0b', weight: 2, fill: false }
				}).addTo(map)
			);
		}
		if (props.footprint) {
			overlays.push(
				L.geoJSON(props.footprint as unknown as GeoJsonObject, {
					style: { color: '#2563eb', weight: 1, fillColor: '#3b82f6', fillOpacity: 0.35 }
				}).addTo(map)
			);
		}
		if (props.marker) {
			const pin: [number, number] = [props.marker[1], props.marker[0]];
			overlays.push(
				L.circleMarker(pin, {
					radius: 7,
					color: '#dc2626',
					weight: 2,
					fillColor: '#ef4444',
					fillOpacity: 0.9
				}).addTo(map)
			);
		}

		if (overlays.length) {
			try {
				const bounds = L.featureGroup(overlays).getBounds();
				map.fitBounds(bounds, { maxZoom: 20, padding: [24, 24] });
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
	class="my-2 w-full overflow-hidden rounded-xl border border-gray-100 dark:border-gray-800"
	data-block-id={blockId}
	data-message-id={messageId}
	data-message-done={messageDone}
>
	<div bind:this={mapElement} class="h-96 w-full z-0"></div>
</div>
