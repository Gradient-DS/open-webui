// AUTO-GENERATED FILE — do not edit by hand.
// Regenerate with: npm run generate:ui-schemas
// Source schema: map.schema.json

export interface MapProps {
	/** PDOK basemap: 'luchtfoto' (aerial) or 'brt' (topographic). */
	basemap?: string;
	/** [lon, lat] map centre (WGS84). */
	center?: Array<number>;
	/** GeoJSON building footprint geometry (EPSG:4326). */
	footprint?: Record<string, unknown> | null;
	/** [lon, lat] address pin. */
	marker?: Array<number> | null;
	/** GeoJSON cadastral parcel geometry (EPSG:4326). */
	parcel?: Record<string, unknown> | null;
	/** Initial zoom level. */
	zoom?: number;
}
