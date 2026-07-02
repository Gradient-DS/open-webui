// AUTO-GENERATED FILE — do not edit by hand.
// Regenerate with: npm run generate:ui-schemas
// Source schema: building3d.schema.json

export interface Building3dProps {
	/** [lon, lat] of the building (WGS84). */
	center?: Array<number>;
	/** Initial camera height above the building (metres). */
	height?: number;
	/** BAG pand id to highlight in the 3D scene. */
	pandid?: string | null;
	/** 3DBAG LoD2.2 Cesium 3D Tiles tileset URL. */
	tileset_url?: string;
}
