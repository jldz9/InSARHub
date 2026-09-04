// Geometry utilities — WKT conversion and bbox extraction

export type Bbox = [number, number, number, number]  // [west, south, east, north]

export function geometryToWkt(geometry: GeoJSON.Geometry): string {
  if (geometry.type === 'Point') {
    const [lng, lat] = geometry.coordinates as number[]
    return `POINT (${lng} ${lat})`
  }
  if (geometry.type === 'Polygon') {
    const rings = (geometry.coordinates as number[][][]).map(ring =>
      ring.map(([lng, lat]) => `${lng} ${lat}`).join(', ')
    )
    return `POLYGON ((${rings.join('), (')}))`
  }
  if (geometry.type === 'MultiPolygon') {
    // Use first polygon
    const rings = (geometry.coordinates as number[][][][])[0].map(ring =>
      ring.map(([lng, lat]) => `${lng} ${lat}`).join(', ')
    )
    return `POLYGON ((${rings.join('), (')}))`
  }
  throw new Error(`Unsupported geometry type: ${geometry.type}`)
}

export function bboxToWkt([w, s, e, n]: Bbox): string {
  return `POLYGON ((${w} ${s}, ${e} ${s}, ${e} ${n}, ${w} ${n}, ${w} ${s}))`
}

export function getGeometryBbox(geometry: GeoJSON.Geometry): Bbox {
  let coords: number[][] = []
  if (geometry.type === 'Point') {
    coords = [geometry.coordinates as number[]]
  } else if (geometry.type === 'Polygon') {
    coords = (geometry.coordinates as number[][][])[0]
  } else if (geometry.type === 'MultiPolygon') {
    coords = (geometry.coordinates as number[][][][]).flat(2)
  }
  const lngs = coords.map(c => c[0])
  const lats  = coords.map(c => c[1])
  return [Math.min(...lngs), Math.min(...lats), Math.max(...lngs), Math.max(...lats)]
}

export function wktToGeometry(wkt: string): GeoJSON.Geometry {
  const text = wkt.trim()

  // ── POINT ─────────────────────────────────────────────
  const pointMatch = text.match(
    /^POINT\s*\(\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*\)$/i
  )
  if (pointMatch) {
    const lng = Number(pointMatch[1])
    const lat = Number(pointMatch[2])

    if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
      throw new Error('Invalid POINT coordinates')
    }

    if (lng < -180 || lng > 180) {
      throw new Error('Longitude must be between -180 and 180')
    }

    if (lat < -90 || lat > 90) {
      throw new Error('Latitude must be between -90 and 90')
    }

    return {
      type: 'Point',
      coordinates: [lng, lat],
    }
  }

  // ── POLYGON ───────────────────────────────────────────
  const polygonMatch = text.match(
    /^POLYGON\s*\(\((.*)\)\)$/is
  )

  if (polygonMatch) {
    const ringTexts = polygonMatch[1].split(/\)\s*,\s*\(/)

    const rings = ringTexts.map(ringText => {
      const coords = ringText.split(',').map(pair => {
        const parts = pair.trim().split(/\s+/)

        if (parts.length < 2) {
          throw new Error('Invalid POLYGON coordinate')
        }

        const lng = Number(parts[0])
        const lat = Number(parts[1])

        if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
          throw new Error('Invalid POLYGON coordinate')
        }

        return [lng, lat]
      })

      if (coords.length < 4) {
        throw new Error('POLYGON requires at least 4 coordinates')
      }

      return coords
    })

    return {
      type: 'Polygon',
      coordinates: rings,
    }
  }

  throw new Error(
    'Unsupported WKT. Currently supports POINT and POLYGON.'
  )
}
