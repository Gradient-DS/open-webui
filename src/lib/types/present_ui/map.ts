// HAND-MAINTAINED for the demo/kadaster branch — mirrors
// soev-solutions/apps/kadaster/agents/components/map.py (MapProps).
// The generate-ui-schemas codegen reads core's registry, which the
// kadaster app deliberately does not touch, so this file is not generated.

export interface MapMarker {
	lon: number;
	lat: number;
	label?: string;
}

export interface MapBuilding {
	/** BAG pand id — the click-to-ask key. */
	pandid: string;
	/** GeoJSON geometry (EPSG:4326, [lon, lat]). */
	geometry: Record<string, unknown>;
	bouwjaar?: number | null;
	gebruiksdoel?: string | null;
	/** True for the building the question is about. */
	target?: boolean;
}

export interface MapProps {
	/** PDOK basemap: 'luchtfoto' (aerial) or 'brt' (topographic). */
	basemap?: 'luchtfoto' | 'brt' | string;
	/** [lon, lat] map centre (WGS84). */
	center?: Array<number>;
	/** Initial zoom level. */
	zoom?: number;
	/** Labelled pins. */
	markers?: MapMarker[];
	/** GeoJSON geometry of the target building (EPSG:4326). */
	footprint?: Record<string, unknown> | null;
	/** GeoJSON geometry of the cadastral parcel (EPSG:4326). */
	parcel?: Record<string, unknown> | null;
	/** Surrounding BAG footprints (clickable). */
	buildings?: MapBuilding[];
	/** Colour the buildings by construction year. */
	color_by?: 'none' | 'bouwjaar' | string;
	/** Radius (m) the buildings were fetched within; 0 = n/a. */
	radius_m?: number;
	/** Years for the aerial-photo slider; empty = no slider. */
	aerial_years?: number[];
	/** Clicking a building asks about it. */
	clickable?: boolean;
}
