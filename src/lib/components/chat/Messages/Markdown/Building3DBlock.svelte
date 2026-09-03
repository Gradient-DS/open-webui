<!--
	Generative-UI `building3d` widget — an interactive CesiumJS view of a
	Dutch building, rendered inline from an agent `present_ui` event.

	Props (Building3DProps) arrive validated from the agent service
	(assembled by the kadaster `show_building_3d` tool). CesiumJS streams
	3DBAG LoD2.2 3D Tiles client-side and the camera is flown to frame the
	building. Cesium is lazy-imported so it stays code-split; the basemap
	is PDOK aerial (no Cesium-ion token required).
-->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { Viewer } from 'cesium';
	import type { Building3dProps } from '$lib/types/present_ui';

	// Typed props passed straight through by PresentUIDispatcher.
	// blockId/messageId/messageDone are surfaced as DOM data attributes.
	export let props: Building3dProps;
	export let blockId: string = '';
	export let messageId: string = '';
	export let messageDone: boolean = false;

	const NL_CENTER: [number, number] = [5.387, 52.156];
	// Fallback when the (always-sent) prop is absent — pinned 3DBAG version.
	const DEFAULT_TILESET = 'https://data.3dbag.nl/v20250903/cesium3dtiles/lod22/tileset.json';
	// Metres to lower the 3DBAG tileset so building bases meet the PDOK aerial:
	// the NL geoid undulation (NAP → WGS84 ellipsoid the imagery is draped on).
	// Exact for sea-level terrain; higher-terrain addresses keep a small float.
	const GEOID_OFFSET = -43.5;

	let container: HTMLDivElement;
	let viewer: Viewer | null = null;

	onMount(async () => {
		const Cesium = await import('cesium');
		await import('cesium/Build/Cesium/Widgets/widgets.css');

		const lon = props.center?.[0] ?? NL_CENTER[0];
		const lat = props.center?.[1] ?? NL_CENTER[1];

		// PDOK aerial (EPSG:3857) as the base layer — avoids the Cesium-ion
		// default imagery (which needs a token).
		const pdok = new Cesium.UrlTemplateImageryProvider({
			url: 'https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/Actueel_ortho25/EPSG:3857/{z}/{x}/{y}.jpeg',
			tilingScheme: new Cesium.WebMercatorTilingScheme(),
			maximumLevel: 19
		});

		viewer = new Cesium.Viewer(container, {
			baseLayer: Cesium.ImageryLayer.fromProviderAsync(Promise.resolve(pdok)),
			baseLayerPicker: false,
			geocoder: false,
			homeButton: false,
			sceneModePicker: false,
			navigationHelpButton: false,
			animation: false,
			timeline: false,
			fullscreenButton: false,
			selectionIndicator: false,
			infoBox: false
		});

		// 3DBAG LoD2.2 buildings (their own ECEF CRS — Cesium places them).
		try {
			const tileset = await Cesium.Cesium3DTileset.fromUrl(props.tileset_url ?? DEFAULT_TILESET);
			viewer.scene.primitives.add(tileset);

			// Seat the buildings on the aerial. 3DBAG places them at their true
			// height (NAP + the ~43.5 m NL geoid), but the PDOK imagery is
			// draped on the ellipsoid (height 0), so the buildings float ~the
			// geoid above the photo. Lower the whole tileset by that amount so
			// the footprints line up with the satellite image underneath.
			const lon0 = props.center?.[0] ?? NL_CENTER[0];
			const lat0 = props.center?.[1] ?? NL_CENTER[1];
			const surface = Cesium.Cartesian3.fromDegrees(lon0, lat0, 0);
			const lowered = Cesium.Cartesian3.fromDegrees(lon0, lat0, GEOID_OFFSET);
			tileset.modelMatrix = Cesium.Matrix4.fromTranslation(
				Cesium.Cartesian3.subtract(lowered, surface, new Cesium.Cartesian3())
			);
			// Highlight the building of concern. 3DBAG exposes the BAG pand
			// id as the `identificatie` feature property, prefixed
			// `NL.IMBAG.Pand.<pandid>`. REPLACE blend so the colour actually
			// shows (the default HIGHLIGHT mode multiplies, hiding it against
			// the baked colour). No fallback condition, so every other
			// building keeps its original colour and only the target changes.
			if (props.pandid) {
				tileset.colorBlendMode = Cesium.Cesium3DTileColorBlendMode.REPLACE;
				tileset.style = new Cesium.Cesium3DTileStyle({
					color: {
						conditions: [
							[`\${identificatie} === 'NL.IMBAG.Pand.${props.pandid}'`, "color('cyan')"]
						]
					}
				});
			}
		} catch {
			// 3D tiles failed to stream — keep the aerial view rather than error.
		}

		// Frame the building obliquely (centred — distinguishes the target).
		const target = Cesium.Cartesian3.fromDegrees(lon, lat, 0);
		viewer.camera.flyToBoundingSphere(new Cesium.BoundingSphere(target, 110), {
			offset: new Cesium.HeadingPitchRange(
				Cesium.Math.toRadians(20),
				Cesium.Math.toRadians(-35),
				320
			),
			duration: 1.5
		});
	});

	onDestroy(() => {
		if (viewer && !viewer.isDestroyed()) {
			viewer.destroy();
		}
		viewer = null;
	});
</script>

<div
	class="relative my-2 w-full overflow-hidden rounded-xl border border-gray-100 dark:border-gray-800"
	data-block-id={blockId}
	data-message-id={messageId}
	data-message-done={messageDone}
>
	<div bind:this={container} class="h-96 w-full"></div>
	<div
		class="pointer-events-none absolute bottom-1 left-2 z-10 rounded bg-black/40 px-1.5 py-0.5 text-[10px] text-white"
	>
		3DBAG — TU Delft · © Kadaster / PDOK
	</div>
</div>
