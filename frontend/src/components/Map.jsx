import { useEffect, useMemo, useState } from 'react';
import DeckGL from '@deck.gl/react';
import { PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import Map from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

const INITIAL_VIEW_STATE = {
  longitude: 77.5946,
  latitude: 12.9716,
  zoom: 11.7,
  pitch: 28,
  bearing: 0,
};

const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
const ROUTE_PALETTE = [
  [6, 182, 212],    // Cyan
  [234, 179, 8],    // Yellow
  [34, 197, 94],    // Green
  [239, 68, 68],    // Red
  [168, 85, 247],   // Purple
];

const scoreColor = (score, alpha = 210) => {
  if (score >= 85) return [127, 29, 29, alpha]; // Dark Red
  if (score >= 65) return [220, 38, 38, alpha]; // Red
  if (score >= 40) return [249, 115, 22, alpha]; // Orange
  return [250, 204, 21, alpha]; // Yellow
};

const routeColor = (route, index, alpha = 235) => [
  ...(route.color || ROUTE_PALETTE[index % ROUTE_PALETTE.length]),
  alpha,
];

const normalizePolygon = (polygon) => {
  if (!Array.isArray(polygon) || polygon.length === 0) return [];
  if (Array.isArray(polygon[0]) && typeof polygon[0][0] === 'number') return polygon;
  if (Array.isArray(polygon[0]) && Array.isArray(polygon[0][0])) return polygon[0];
  return [];
};

const buildRouteStops = (routes) => routes.flatMap((route, routeIndex) => {
  if (Array.isArray(route.stops) && route.stops.length > 0) {
    return route.stops.map((stop, stopIndex) => ({
      ...stop,
      position: stop.centroid,
      routeIndex,
      stopIndex,
      color: route.color || ROUTE_PALETTE[routeIndex % ROUTE_PALETTE.length],
      kind: 'route-stop',
      vehicleId: route.vehicle_id,
    }));
  }

  return (route.path || []).slice(1, -1).map((position, stopIndex) => ({
    position,
    routeIndex,
    stopIndex,
    color: route.color || ROUTE_PALETTE[routeIndex % ROUTE_PALETTE.length],
    kind: 'route-stop',
    vehicleId: route.vehicle_id,
  }));
});

const tooltipNumber = (value, digits = 1) => (
  value === undefined || value === null ? '0' : Number(value).toFixed(digits)
);

const MapComponent = ({ clusters, events = [], selectedEventId, eventDetails, routes, summary, metrics, selectedPoint, onMapClick, interactiveCursor }) => {
  const [pulse, setPulse] = useState(1);

  useEffect(() => {
    const interval = setInterval(() => {
      setPulse((p) => (p >= 1.4 ? 1 : p + 0.025));
    }, 40);
    return () => clearInterval(interval);
  }, []);

  const hotspotData = useMemo(
    () => clusters
      .map((cluster) => ({ ...cluster, polygon: normalizePolygon(cluster.polygon) }))
      .filter((cluster) => cluster.polygon.length >= 3),
    [clusters],
  );

  const eventImpactZones = useMemo(() => {
    if (!eventDetails || !eventDetails.prediction || !eventDetails.prediction.affected_zones) return [];
    return eventDetails.prediction.affected_zones;
  }, [eventDetails]);

  const routedHotspots = useMemo(
    () => clusters.filter((cluster) => cluster.is_routed),
    [clusters],
  );

  const routePathData = useMemo(
    () => routes.filter((route) => Array.isArray(route.path) && route.path.length > 1),
    [routes],
  );

  const routeStops = useMemo(() => buildRouteStops(routes), [routes]);
  const roadRouteCount = summary?.road_routed_patrols ?? routes.filter((route) => route.geometry_source === 'road').length;
  const visibleHotspots = summary?.returned_hotspots ?? clusters.length;
  const totalHotspots = summary?.active_hotspots ?? clusters.length;

  const layers = useMemo(() => [
    new ScatterplotLayer({
      id: 'event-impact-zones',
      data: eventImpactZones,
      pickable: false,
      getPosition: d => [d.longitude, d.latitude],
      getFillColor: d => scoreColor(d.severity_score, 40),
      getLineColor: d => scoreColor(d.severity_score, 180),
      getLineWidth: 2,
      stroked: true,
      radiusUnits: 'meters',
      getRadius: d => d.radius_km * 1000,
    }),

    new ScatterplotLayer({
      id: 'event-markers',
      data: events,
      pickable: true,
      getPosition: d => [d.longitude, d.latitude],
      getFillColor: d => d.id === selectedEventId ? [255, 255, 255, 255] : scoreColor(d.severity === 'critical' ? 90 : d.severity === 'high' ? 70 : 50, 255),
      getLineColor: [0, 0, 0, 255],
      getLineWidth: 2,
      stroked: true,
      radiusUnits: 'meters',
      getRadius: d => d.id === selectedEventId ? 300 : 200,
      radiusMinPixels: 6,
    }),

    new TextLayer({
        id: 'event-labels',
        data: events,
        getPosition: d => [d.longitude, d.latitude],
        getText: d => d.title,
        getSize: 12,
        getColor: [255, 255, 255, 255],
        getPixelOffset: [0, -25],
        getTextAnchor: 'middle',
        getAlignmentBaseline: 'bottom',
    }),
    new ScatterplotLayer({
      id: 'critical-hotspot-pulse',
      data: clusters.filter((c) => c.impact_score >= 85),
      pickable: false,
      getPosition: (cluster) => cluster.centroid,
      getFillColor: [127, 29, 29, 40],
      getLineColor: [127, 29, 29, 200],
      getLineWidth: 2,
      stroked: true,
      radiusUnits: 'meters',
      getRadius: (cluster) => (150 + cluster.impact_score * 2) * pulse,
      updateTriggers: {
        getRadius: [pulse],
      },
    }),

    new PolygonLayer({
      id: 'hotspot-polygons',
      data: hotspotData,
      pickable: true,
      stroked: true,
      filled: true,
      getPolygon: (cluster) => cluster.polygon,
      getLineColor: (cluster) => scoreColor(cluster.impact_score, cluster.is_routed ? 255 : 200),
      getFillColor: (cluster) => scoreColor(cluster.impact_score, cluster.is_routed ? 100 : 60),
      getLineWidth: (cluster) => (cluster.is_routed ? 6 : Math.max(2, Math.min(6, cluster.impact_score / 18))),
      lineWidthMinPixels: 1.5,
      transitions: {
        getFillColor: 500,
        getLineColor: 500,
      },
    }),

    new ScatterplotLayer({
      id: 'routed-hotspot-rings',
      data: routedHotspots,
      pickable: false,
      getPosition: (cluster) => cluster.centroid,
      getFillColor: [255, 255, 255, 0],
      getLineColor: [255, 255, 255, 240],
      getLineWidth: 4,
      stroked: true,
      radiusUnits: 'meters',
      getRadius: (cluster) => Math.max(130, Math.min(360, 110 + cluster.impact_score * 2.2)),
      radiusMinPixels: 14,
      radiusMaxPixels: 36,
    }),

    new ScatterplotLayer({
      id: 'hotspot-centroids',
      data: clusters,
      pickable: true,
      getPosition: (cluster) => cluster.centroid,
      getFillColor: (cluster) => scoreColor(cluster.impact_score, cluster.is_routed ? 255 : 220),
      getLineColor: (cluster) => (cluster.is_routed ? [255, 255, 255, 255] : [255, 255, 255, 200]),
      getLineWidth: (cluster) => (cluster.is_routed ? 4 : 1.5),
      stroked: true,
      radiusUnits: 'meters',
      getRadius: (cluster) => Math.max(45, Math.min(220, 30 + cluster.impact_score * 2.0)),
      radiusMinPixels: 4,
      radiusMaxPixels: 20,
    }),

    new PathLayer({
      id: 'patrol-route-casing',
      data: routePathData,
      pickable: false,
      getPath: (route) => route.path,
      getColor: [8, 13, 23, 200],
      widthUnits: 'pixels',
      getWidth: (route) => (route.geometry_source === 'road' ? 6 : 5),
      widthMinPixels: 4,
      widthMaxPixels: 8,
      capRounded: true,
      jointRounded: true,
    }),

    new PathLayer({
      id: 'patrol-routes',
      data: routePathData,
      pickable: true,
      getPath: (route) => route.path,
      getColor: (route, { index }) => routeColor(route, index, route.geometry_source === 'road' ? 255 : 200),
      widthUnits: 'pixels',
      getWidth: (route) => (route.geometry_source === 'road' ? 4 : 3),
      widthMinPixels: 2,
      widthMaxPixels: 5,
      capRounded: true,
      jointRounded: true,
      transitions: {
        getPath: 450,
      },
    }),

    new ScatterplotLayer({
      id: 'route-stop-rings',
      data: routeStops,
      pickable: false,
      getPosition: (stop) => stop.position,
      getFillColor: [255, 255, 255, 245],
      getLineColor: [8, 13, 23, 255],
      getLineWidth: 2,
      stroked: true,
      radiusUnits: 'meters',
      getRadius: 112,
      radiusMinPixels: 11,
      radiusMaxPixels: 25,
    }),

    new ScatterplotLayer({
      id: 'route-stops',
      data: routeStops,
      pickable: true,
      getPosition: (stop) => stop.position,
      getFillColor: (stop) => [...stop.color, 245],
      getLineColor: [8, 13, 23, 255],
      getLineWidth: 2,
      stroked: true,
      radiusUnits: 'meters',
      getRadius: 68,
      radiusMinPixels: 7,
      radiusMaxPixels: 17,
    }),

    new ScatterplotLayer({
      id: 'depot-marker',
      data: [{ position: [77.5946, 12.9716], kind: 'depot' }],
      pickable: true,
      getPosition: (point) => point.position,
      getFillColor: [15, 23, 42, 245],
      getLineColor: [255, 255, 255, 255],
      getLineWidth: 2,
      stroked: true,
      radiusUnits: 'meters',
      getRadius: 120,
      radiusMinPixels: 8,
      radiusMaxPixels: 18,
    }),

    new ScatterplotLayer({
      id: 'selected-anomaly',
      data: selectedPoint ? [{ position: selectedPoint, kind: 'selected-anomaly' }] : [],
      pickable: true,
      getPosition: (point) => point.position,
      getFillColor: [239, 68, 68, 230],
      getLineColor: [255, 255, 255, 255],
      getLineWidth: 2,
      stroked: true,
      radiusUnits: 'meters',
      getRadius: 150,
      radiusMinPixels: 10,
      radiusMaxPixels: 30,
    }),

    // LABELS MOVED TO END TO ENSURE THEY ARE ON TOP
    new TextLayer({
      id: 'route-stop-labels',
      data: routeStops,
      getPosition: (stop) => stop.position,
      getText: (stop) => `${stop.routeIndex + 1}.${stop.stopIndex + 1}`,
      getSize: 10,
      getColor: [8, 13, 23, 255],
      getTextAnchor: 'middle',
      getAlignmentBaseline: 'center',
      getPixelOffset: [0, 0],
      fontWeight: 800,
      fontFamily: 'monospace',
      billboard: true,
      pickable: false,
    }),

    new TextLayer({
      id: 'depot-label',
      data: [{ position: [77.5946, 12.9716] }],
      getPosition: (point) => point.position,
      getText: () => 'HQ',
      getSize: 11,
      getColor: [255, 255, 255, 255],
      getTextAnchor: 'middle',
      getAlignmentBaseline: 'center',
      getPixelOffset: [0, 0],
      fontWeight: 900,
      fontFamily: 'monospace',
      billboard: true,
      pickable: false,
    }),
  ], [clusters, events, selectedEventId, eventImpactZones, hotspotData, routePathData, routeStops, routedHotspots, selectedPoint, pulse]);

  const getTooltip = ({ object }) => {
    if (!object) return null;

    if (object.event_type) {
        return {
          html: `
            <div class="custom-tooltip event-tooltip">
              <div class="tooltip-header">${object.title}</div>
              <div class="tooltip-grid">
                <span>Type</span><b>${object.event_type}</b>
                <span>Severity</span><b class="text-red">${object.severity}</b>
                <span>Planned</span><b>${object.is_planned ? 'Yes' : 'No'}</b>
              </div>
              <p>${object.description || ''}</p>
            </div>
          `,
          className: 'deck-tooltip',
        };
    }

    if (object.kind === 'route-stop') {
      return {
        html: `
          <strong>${object.vehicleId || 'Patrol'}</strong>
          <div>Stop ${object.stopIndex + 1}</div>
          <div>Impact ${tooltipNumber(object.impact_score, 1)}</div>
        `,
        className: 'deck-tooltip',
      };
    }

    if (object.kind === 'depot') {
      return {
        html: '<strong>Dispatch depot</strong><div>Patrol routes start and return here.</div>',
        className: 'deck-tooltip',
      };
    }

    if (object.kind === 'selected-anomaly') {
      return {
        html: '<strong>Staged anomaly</strong><div>Press Simulate to insert and re-optimize.</div>',
        className: 'deck-tooltip',
      };
    }

    if (object.impact_score !== undefined) {
      const recovery = object.intervention_benefit?.recovery_metrics;
      return {
        html: `
          <div class="custom-tooltip">
            <div class="tooltip-header">Zone ${object.cluster_id} - ${object.priority || 'Medium'}</div>
            <div class="tooltip-score-row">
              <div class="score-block">
                <span class="label">Impact Score</span>
                <span class="value">${tooltipNumber(object.impact_score, 1)}</span>
              </div>
              <div class="score-block recovery">
                <span class="label">Est. Recovery</span>
                <span class="value">+${tooltipNumber(recovery?.estimated_capacity_recovered_vph, 0)} vph</span>
              </div>
            </div>
            <div class="tooltip-grid">
              <span>Capacity Loss</span><b>${tooltipNumber(object.intervention_benefit?.before?.capacity_loss_percent, 1)}%</b>
              <span>After Enforcement</span><b class="text-green">${tooltipNumber(object.intervention_benefit?.after?.capacity_loss_percent, 1)}%</b>
              <span>Throughput Gain</span><b class="text-blue">${tooltipNumber(recovery?.estimated_capacity_recovered_vph, 0)} vph</b>
              <span>Violations</span><b>${object.raw_count || 0}</b>
              <span>Recommendation</span><b style="color: #fbbf24">${object.recommended_action || 'Monitoring'}</b>
            </div>
            <div class="tooltip-reasoning">
              <strong>Why flagged?</strong>
              <p>${(() => {
                const heavyPct = Math.round((object.heavy_vehicle_share || 0) * 100);
                if (object.impact_score > 80) return `Severe carriageway obstruction — heavy vehicles causing >30% capacity loss (TTI ${object.travel_time_index?.toFixed(2) ?? '—'}).`;
                if ((object.heavy_vehicle_share || 0) > 0.4) return `${heavyPct}% heavy vehicle mix (${object.top_vehicle_type || 'mixed'}) amplifies lane restriction severity.`;
                if ((object.raw_count || 0) > 30) return `High violation density (${object.raw_count} incidents) compounding peak-hour queue delay.`;
                if ((object.stochastic_delay_score || 0) > 40) return `Stochastic obstruction probability ${Math.round((object.active_obstruction_probability || 0) * 100)}% — measurable delay at this junction.`;
                return `Recurring parking pattern (${object.raw_count || 0} violations, ${object.top_vehicle_type || 'mixed'}) generating measurable queue delay.`;
              })()}</p>
            </div>
          </div>
        `,
        className: 'deck-tooltip',
      };
    }

    if (object.path) {
      return {
        html: `
          <strong>${object.vehicle_id}</strong>
          <div>${object.stop_count} stops, ${tooltipNumber(object.distance_km, 1)} km${object.duration_minutes ? `, ${tooltipNumber(object.duration_minutes, 1)} min` : ''}</div>
          <div>${object.geometry_source === 'road' ? 'Road-following geometry' : 'Direct-line fallback'}</div>
        `,
        className: 'deck-tooltip',
      };
    }

    return null;
  };

  return (
    <div className="map-root">
      <div className="map-kpi-strip">
        <div className="kpi-item">
          <span className="kpi-label">Active Hotspots</span>
          <span className="kpi-value">{totalHotspots}</span>
        </div>
        <div className="kpi-item">
          <span className="kpi-label">Total Impact</span>
          <span className="kpi-value text-red">{summary?.total_network_impact || 0}</span>
        </div>
        <div className="kpi-item">
          <span className="kpi-label">Capacity Gain</span>
          <span className="kpi-value text-blue">{tooltipNumber(summary?.total_capacity_gain_vph, 0)} vph</span>
        </div>
        <div className="kpi-item">
          <span className="kpi-label">Pipeline</span>
          <span className="kpi-value">{tooltipNumber(metrics?.total_pipeline_ms, 0)}ms</span>
        </div>
      </div>

      <DeckGL
        initialViewState={INITIAL_VIEW_STATE}
        controller
        layers={layers}
        getTooltip={getTooltip}
        onClick={onMapClick}
        getCursor={({ isDragging }) => (isDragging ? 'grabbing' : interactiveCursor)}
      >
        <Map mapStyle={MAP_STYLE} />
      </DeckGL>

      <div className="map-legend">
        <div className="map-status-grid">
          <span><b>{visibleHotspots}</b> shown</span>
          <span><b>{totalHotspots}</b> total</span>
          <span><b>{roadRouteCount}/{routes.length}</b> road</span>
        </div>
        <div className="legend-title">Congestion Impact</div>
        <div className="legend-gradient" />
        <div className="legend-scale">
          <span>Low</span>
          <span>Critical</span>
        </div>
        <div className="legend-item"><span className="legend-line" /> Patrol route</div>
        <div className="legend-item"><span className="legend-ring" /> Routed hotspot</div>
        <div className="legend-item"><span className="legend-dot" /> Patrol stop</div>
      </div>
    </div>
  );
};

export default MapComponent;
