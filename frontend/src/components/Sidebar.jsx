import { useState, useEffect } from 'react';
import {
  AlertTriangle,
  ArrowUpRight,
  Car,
  Clock,
  Gauge,
  MapPin,
  Navigation,
  Play,
  Route,
  Settings2,
  Calendar,
  ShieldCheck,
  CheckCircle2,
  TrendingUp,
  Users,
  Siren,
  BarChart3,
  Zap,
} from 'lucide-react';

const formatNumber = (value, digits = 0) => {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return '0';
  return Number(value).toFixed(digits);
};

const ParameterInput = ({ label, value, min, max, step = 1, onChange }) => (
  <label className="parameter-control">
    <span>{label}</span>
    <input
      type="number"
      min={min}
      max={max}
      step={step}
      value={value ?? ''}
      onChange={(event) => onChange(event.target.value === '' ? null : event.target.value)}
    />
  </label>
);

const SelectInput = ({ label, value, options, onChange }) => (
  <label className="parameter-control">
    <span>{label}</span>
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map(({ value: v, label: l }) => (
        <option key={v} value={v}>{l}</option>
      ))}
    </select>
  </label>
);

const VEHICLE_OPTIONS = [
  { value: 'CAR',          label: 'Car' },
  { value: 'TANKER',       label: 'Tanker' },
  { value: 'BUS',          label: 'Bus' },
  { value: 'LORRY',        label: 'Lorry / Truck' },
  { value: 'AUTO',         label: 'Auto Rickshaw' },
  { value: 'SCOOTER',      label: 'Scooter / Two-wheeler' },
];

const VIOLATION_OPTIONS = [
  { value: 'WRONG PARKING',  label: 'Wrong Parking' },
  { value: 'DOUBLE PARKING', label: 'Double Parking' },
  { value: 'NO PARKING',     label: 'No Parking Zone' },
];

const EVENT_TYPE_OPTIONS = [
  { value: 'SINGLE',       label: 'Single vehicle' },
  { value: 'TANKER_SPILL', label: 'Tanker spill cluster (×12 heavies)' },
  { value: 'STADIUM_SURGE',label: 'Stadium surge (×40 cars)' },
];

const SEVERITY_COLOR = {
  critical: '#ef4444',
  high: '#f97316',
  medium: '#eab308',
  low: '#22c55e',
};

const EVENT_TYPE_ICON = {
  sports: '🏟', concert: '🎵', festival: '🎉', political: '📢',
  accident: '🚨', construction: '🚧', rally: '📣', protest: '✊',
};

function ForecastTimeline({ forecast }) {
  if (!forecast?.forecast_points?.length) return null;

  const points = forecast.forecast_points;
  const peak = forecast.summary?.peak_risk_score ?? 0;

  return (
    <div className="forecast-timeline">
      <div className="forecast-header">
        <TrendingUp size={14} />
        <span>Congestion Forecast</span>
        <span className="forecast-current-badge" style={{ background: SEVERITY_COLOR[forecast.current_congestion_level] ?? '#666' }}>
          {forecast.status?.toUpperCase()}
        </span>
      </div>

      <div className="forecast-points">
        {points.map((pt) => {
          const barH = peak > 0 ? Math.round((pt.risk_score / peak) * 52) : 4;
          return (
            <div key={pt.label} className="forecast-point" title={pt.recommended_action}>
              <div className="fp-bar-wrap">
                <div className="fp-bar" style={{ height: `${Math.max(4, barH)}px`, background: pt.color }} />
              </div>
              <span className="fp-score" style={{ color: pt.color }}>{pt.risk_score}</span>
              <span className="fp-label">{pt.label}</span>
            </div>
          );
        })}
      </div>

      <div className="forecast-peak-row">
        <div className="fpeak-item">
          <Users size={12} />
          <span>{forecast.summary?.peak_manpower} officers at peak</span>
        </div>
        <div className="fpeak-item">
          <Siren size={12} />
          <span>{forecast.summary?.peak_barricades} barricades</span>
        </div>
      </div>

      <div className="forecast-action-box" style={{ borderColor: SEVERITY_COLOR[forecast.current_congestion_level] ?? '#444' }}>
        <span className="fab-label">NOW —</span>
        <span className="fab-text">{forecast.current_action}</span>
      </div>

      {forecast.model_provenance && (
        <div className="forecast-provenance">
          {forecast.model_provenance.curve_source === 'empirical' ? (
            <>
              <span className="provenance-badge empirical">Empirically derived</span>
              <span className="provenance-detail">
                {forecast.model_provenance.event_day_rows?.toLocaleString()} event-day observations · Bangalore Traffic Pulse dataset
              </span>
            </>
          ) : (
            <>
              <span className="provenance-badge heuristic">HCM heuristic</span>
              {forecast.model_provenance.spatial_validation?.max_uplift_ratio && (
                <span className="provenance-detail">
                  Spatial risk validated: {forecast.model_provenance.spatial_validation.max_uplift_ratio}x violation uplift on IPL match days ({forecast.model_provenance.spatial_validation.total_records?.toLocaleString()} records)
                </span>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function LearningPanel({ learning }) {
  if (!learning?.has_outcome) return null;

  const predicted = learning.predicted_score;
  const actual = learning.actual_score;
  const accuracy = learning.effectiveness_score;
  const maxBar = Math.max(predicted, actual, 1);

  return (
    <div className="learning-panel">
      <div className="panel-title">
        <BarChart3 size={15} />
        <h2>Post-Event Learning</h2>
      </div>

      <div className="learning-bars">
        <div className="lbar-row">
          <span className="lbar-label">Predicted</span>
          <div className="lbar-track">
            <div className="lbar-fill predicted" style={{ width: `${(predicted / maxBar) * 100}%` }} />
          </div>
          <span className="lbar-val">{predicted?.toFixed(1)}</span>
        </div>
        <div className="lbar-row">
          <span className="lbar-label">Actual</span>
          <div className="lbar-track">
            <div className="lbar-fill actual" style={{ width: `${(actual / maxBar) * 100}%` }} />
          </div>
          <span className="lbar-val">{actual?.toFixed(1)}</span>
        </div>
      </div>

      <div className="learning-accuracy">
        <span className="acc-value" style={{ color: accuracy >= 75 ? '#22c55e' : accuracy >= 50 ? '#eab308' : '#ef4444' }}>
          {accuracy?.toFixed(0)}%
        </span>
        <span className="acc-label">prediction accuracy</span>
      </div>

      {learning.insight && (
        <p className="learning-insight">{learning.insight}</p>
      )}

      {learning.peer_accuracy?.sample_size > 0 && (
        <p className="peer-note">
          Peer avg error ({learning.peer_accuracy.event_type}): ±{learning.peer_accuracy.mean_error} pts
          across {learning.peer_accuracy.sample_size} event{learning.peer_accuracy.sample_size !== 1 ? 's' : ''}
        </p>
      )}
    </div>
  );
}

const Sidebar = ({
  data,
  events = [],
  selectedEventId,
  setSelectedEventId,
  eventDetails,
  eventForecast,
  dashboardSummary,
  liveIncidents,
  parameters,
  onParameterChange,
  selectedPoint,
  setPlacementMode,
  selectedVehicle,
  setSelectedVehicle,
  selectedViolation,
  setSelectedViolation,
  selectedEventType,
  setSelectedEventType,
  onSimulate,
  onFeedback,
  onActivateEvent,
  onApplyParameters,
  onSeedEvents,
  onDemo,
  loading,
}) => {
  const [activeTab, setActiveTab] = useState('hotspots');
  const [localTime, setLocalTime] = useState(parameters.time_hour === null ? -1 : parameters.time_hour);
  const [feedbackImpact, setFeedbackImpact] = useState(50);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [localParams, setLocalParams] = useState({
    min_cluster_size: parameters.min_cluster_size,
    min_samples: parameters.min_samples,
    patrol_vehicles: parameters.patrol_vehicles,
    max_stops: parameters.max_stops,
    clustering_engine: parameters.clustering_engine,
    routing_engine: parameters.routing_engine,
  });

  useEffect(() => {
    setLocalTime(parameters.time_hour === null ? -1 : parameters.time_hour);
  }, [parameters.time_hour]);

  const clusters = data.clusters || [];
  const routes = data.routes || [];
  const summary = data.summary || {};
  const totalClusters = summary.active_hotspots ?? clusters.length;
  const roadRoutedRoutes = summary.road_routed_patrols ?? routes.filter((route) => route.geometry_source === 'road').length;
  const topHotspots = clusters.slice(0, 6);

  return (
    <aside className="sidebar">
      <header className="sidebar-header">
        <div className="brand-row">
          <Gauge size={22} />
          <div>
            <h1>Gridlock Intelligence</h1>
            <p>Predict event-driven congestion. Recommend the right deployment.</p>
          </div>
        </div>
      </header>

      <div className="sidebar-tabs">
        <button
          className={activeTab === 'hotspots' ? 'active' : ''}
          onClick={() => setActiveTab('hotspots')}
        >
          Risk Zones
        </button>
        <button 
          className={activeTab === 'events' ? 'active' : ''} 
          onClick={() => setActiveTab('events')}
        >
          Events
        </button>
        <button 
          className={activeTab === 'settings' ? 'active' : ''} 
          onClick={() => setActiveTab('settings')}
        >
          System
        </button>
      </div>

      <div className="tab-content">
        {activeTab === 'hotspots' && (
          <>
            <section className="panel metric-panel">
              <div className="metric">
                <span className="metric-value">{totalClusters}</span>
                <span className="metric-label">Impact Zones</span>
              </div>
              <div className="metric">
                <span className="metric-value">{formatNumber(summary.patrol_coverage_percent, 1)}%</span>
                <span className="metric-label">Coverage</span>
              </div>
              <div className="metric">
                <span className="metric-value">{formatNumber(summary.total_expected_recovery_vph, 0)}</span>
                <span className="metric-label">Recovery vph</span>
              </div>
            </section>

            <section className="panel">
              <div className="panel-title">
                <Navigation size={17} />
                <h2>High-Risk Impact Zones</h2>
              </div>
              <div className="hotspot-list">
                {topHotspots.map((cluster, index) => (
                  <div className="hotspot-row" key={cluster.cluster_id}>
                    <span className="rank">{index + 1}</span>
                    <div className="hotspot-main">
                      <div className="hotspot-header">
                        <strong>Zone {cluster.cluster_id}</strong>
                        <div className="hotspot-badge-group">
                          <span className={`priority-badge ${cluster.priority?.toLowerCase() || 'low'}`}>
                            {cluster.priority || 'Low'}
                          </span>
                          <span className="impact-value">{formatNumber(cluster.impact_score, 1)}</span>
                        </div>
                      </div>
                      <div className="impact-bar">
                        <span style={{ width: `${Math.min(100, cluster.impact_score)}%` }} />
                      </div>
                      <div className="hotspot-recovery-metric">
                        <ArrowUpRight size={12} className="text-green" />
                        <span>Est. recovery: <b>{formatNumber(cluster.intervention_benefit?.recovery_metrics?.estimated_capacity_recovered_vph, 0)} vph</b></span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="panel">
              <div className="panel-title">
                <Route size={17} />
                <h2>Deployment Routing</h2>
                <span className="panel-kpi">{roadRoutedRoutes}/{routes.length} road-routed</span>
              </div>
              <div className="route-list">
                {routes.map((route) => (
                  <div className="route-item" key={route.vehicle_id}>
                    <span className="route-swatch" style={{ backgroundColor: `rgb(${route.color?.join(',') || '56,189,248'})` }} />
                    <div className="route-info">
                      <strong>{route.vehicle_id}</strong>
                      <span>{route.stop_count} stops, {formatNumber(route.distance_km, 1)} km</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}

        {activeTab === 'events' && (
          <>
            {/* Alert banner — active / imminent events */}
            {dashboardSummary && dashboardSummary.alert_count > 0 && (
              <div className="alert-banner" style={{
                borderColor: SEVERITY_COLOR[dashboardSummary.highest_severity_active] ?? '#f97316',
              }}>
                <Siren size={14} />
                <span>
                  {dashboardSummary.active_events.length > 0 && (
                    <><strong>{dashboardSummary.active_events.length} active</strong>{' '}</>
                  )}
                  {dashboardSummary.imminent_events_2h.length > 0 && (
                    <><strong>{dashboardSummary.imminent_events_2h.length} imminent</strong> (≤2h){' '}</>
                  )}
                  event{dashboardSummary.alert_count !== 1 ? 's' : ''} — ops required
                </span>
              </div>
            )}

            {liveIncidents && liveIncidents.hotspots?.length > 0 && (
              <section className="panel live-incidents-panel">
                <div className="panel-title">
                  <Zap size={17} className="live-pulse" />
                  <h2>Auto-detected Hotspots</h2>
                  <span className="panel-kpi live">{liveIncidents.total_detected} zones</span>
                </div>
                <p className="live-subtext">
                  {liveIncidents.window_days}d window vs {liveIncidents.baseline_days}d baseline ·{' '}
                  min uplift {liveIncidents.min_uplift}×
                </p>
                <div className="live-incident-list">
                  {liveIncidents.hotspots.slice(0, 5).map((h, i) => (
                    <div key={i} className="live-incident-row">
                      <span className={`severity-dot ${h.severity}`} />
                      <div className="live-incident-info">
                        <span className="live-loc">{h.lat.toFixed(2)}°N {h.lon.toFixed(2)}°E</span>
                        <span className="live-stat">{h.recent_count} violations · {h.uplift_ratio}× baseline</span>
                      </div>
                      <span className="live-uplift" style={{ color: h.severity === 'critical' ? '#ef4444' : h.severity === 'high' ? '#f97316' : '#eab308' }}>
                        +{((h.uplift_ratio - 1) * 100).toFixed(0)}%
                      </span>
                    </div>
                  ))}
                </div>
                <p className="live-as-of">
                  Scanned as of {liveIncidents.as_of ? new Date(liveIncidents.as_of).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—'}
                </p>
              </section>
            )}

            <section className="panel">
              <div className="panel-title">
                <Calendar size={17} />
                <h2>Events</h2>
                <span className="panel-kpi">{events.length} total</span>
              </div>
              <div className="event-list">
                {events.length === 0 && (
                  <p className="hint-text" style={{ textAlign: 'left', padding: '8px 0' }}>
                    No events seeded. Use System → Seed Sample Events.
                  </p>
                )}
                {events.map(event => {
                  const now = Date.now();
                  const start = new Date(event.start_time).getTime();
                  const end = event.end_time ? new Date(event.end_time).getTime() : Infinity;
                  const isActive = start <= now && now <= end;
                  const isImminent = !isActive && start > now && (start - now) < 2 * 3600 * 1000;
                  const isPast = end < now;
                  return (
                    <div
                      key={event.id}
                      className={`event-item ${selectedEventId === event.id ? 'selected' : ''}`}
                      onClick={() => setSelectedEventId(event.id === selectedEventId ? null : event.id)}
                    >
                      <div className="event-type-icon" title={event.event_type}>
                        {EVENT_TYPE_ICON[event.event_type] ?? event.event_type[0].toUpperCase()}
                      </div>
                      <div className="event-info">
                        <strong>{event.title}</strong>
                        <span>
                          {isActive ? '● Active · ' : isImminent ? '◎ Imminent · ' : isPast ? '✓ Past · ' : ''}
                          {new Date(event.start_time).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '3px', flexShrink: 0 }}>
                        <span className="severity-badge" style={{ background: `${SEVERITY_COLOR[event.severity]}22`, color: SEVERITY_COLOR[event.severity], border: `1px solid ${SEVERITY_COLOR[event.severity]}44` }}>
                          {event.severity}
                        </span>
                        {event.source === 'unplanned_incident' && (
                          <span className="source-badge unplanned">unplanned</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            {eventDetails && (
              <section className="panel event-details-panel">
                {/* Header */}
                <div className="panel-title">
                  <ShieldCheck size={17} />
                  <h2>
                    {EVENT_TYPE_ICON[eventDetails.event?.event_type] ?? ''} {eventDetails.event?.title}
                  </h2>
                </div>

                {eventDetails.event?.description && (
                  <p className="event-desc">{eventDetails.event.description}</p>
                )}

                {/* Impact summary row */}
                <div className="impact-summary">
                  <div className="impact-score-large" style={{ borderColor: SEVERITY_COLOR[eventDetails.event?.severity] ?? '#888' }}>
                    <span className="value" style={{ color: SEVERITY_COLOR[eventDetails.event?.severity] }}>
                      {eventDetails.prediction?.impact_score?.toFixed(1) ?? '—'}
                    </span>
                    <span className="label">Risk Score</span>
                  </div>
                  <div className="confidence-meter">
                    <div className="meter-bar">
                      <div className="fill" style={{ width: `${(eventDetails.prediction?.confidence_score ?? 0) * 100}%` }} />
                    </div>
                    <span>{Math.round((eventDetails.prediction?.confidence_score ?? 0) * 100)}% Confidence</span>
                    <span style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '2px' }}>
                      {eventDetails.event?.is_planned ? 'Planned — higher certainty' : 'Unplanned — reactive estimate'}
                      {eventDetails.event?.expected_attendance > 0 ? ` · ${(eventDetails.event.expected_attendance / 1000).toFixed(0)}k expected` : ''}
                    </span>
                  </div>
                </div>

                {/* Multi-horizon forecast */}
                <ForecastTimeline forecast={eventForecast} />

                {/* Why risky */}
                {eventDetails.prediction?.why_risky?.length > 0 && (
                  <div className="why-risky-box">
                    {eventDetails.prediction.why_risky.map((r, i) => (
                      <div key={i} className="why-risky-item">
                        <AlertTriangle size={11} />
                        <span>{r}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Score breakdown */}
                {eventDetails.prediction?.score_breakdown && (
                  <div className="score-breakdown">
                    <h3>Impact Score Breakdown</h3>
                    {[
                      ['Event type', eventDetails.prediction.score_breakdown.event_type_base],
                      ['Severity', eventDetails.prediction.score_breakdown.severity_contribution],
                      ['Attendance', eventDetails.prediction.score_breakdown.attendance_contribution],
                      ['Road closure', eventDetails.prediction.score_breakdown.road_closure_bonus],
                    ].filter(([, v]) => v > 0).map(([label, value]) => (
                      <div key={label} className="breakdown-row">
                        <span className="bdr-label">{label}</span>
                        <div className="bdr-track">
                          <div className="bdr-fill" style={{ width: `${Math.min(100, (value / 100) * 100)}%` }} />
                        </div>
                        <span className="bdr-val">+{value}</span>
                      </div>
                    ))}
                    <div className="breakdown-total">
                      <span>Final score</span>
                      <strong>{eventDetails.prediction.score_breakdown.final_score}</strong>
                    </div>
                  </div>
                )}

                {/* Resource recommendations */}
                <div className="recommendations-grid">
                  <div className="rec-card">
                    <span className="rec-value">{eventDetails.prediction?.recommendations?.manpower_count ?? '—'}</span>
                    <span className="rec-label">Officers</span>
                  </div>
                  <div className="rec-card">
                    <span className="rec-value">{eventDetails.prediction?.recommendations?.barricade_count ?? '—'}</span>
                    <span className="rec-label">Barricades</span>
                  </div>
                </div>

                {/* Diversion plan */}
                {eventDetails.prediction?.recommendations?.suggested_diversions?.length > 0 && (
                  <div className="diversions-list">
                    <h3>Diversion Plan</h3>
                    {eventDetails.prediction.recommendations.suggested_diversions.map((d, i) => (
                      <div key={i} className="diversion-item">
                        <ArrowUpRight size={14} />
                        <span>{d}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Demo simulation button */}
                <div className="button-row" style={{ marginBottom: '4px' }}>
                  <button
                    className="btn danger full-width"
                    onClick={() => onActivateEvent(selectedEventId)}
                    disabled={loading}
                  >
                    <AlertTriangle size={14} />
                    Run Impact Simulation
                  </button>
                </div>
                <p className="hint-text" style={{ marginBottom: '8px' }}>
                  Synthetic scenario — injects simulated congestion triggers for this event
                </p>

                {/* Post-event feedback / learning */}
                {!eventDetails.feedback ? (
                  <div className="feedback-section">
                    <h3>Post-Event Feedback</h3>
                    <div className="field">
                      <label>Actual Impact: {feedbackImpact}</label>
                      <input
                        type="range" min="0" max="100"
                        value={feedbackImpact}
                        onChange={(e) => setFeedbackImpact(Number(e.target.value))}
                      />
                    </div>
                    <div className="button-row">
                      <button
                        className="btn primary full-width"
                        onClick={() => onFeedback(selectedEventId, {
                          actual_impact_score: feedbackImpact,
                          actual_severity: feedbackImpact > 70 ? 'critical' : feedbackImpact > 40 ? 'high' : 'medium',
                        })}
                      >
                        Submit Outcome
                      </button>
                    </div>
                  </div>
                ) : (
                  <LearningPanel learning={{
                    has_outcome: true,
                    predicted_score: eventDetails.prediction?.impact_score,
                    actual_score: eventDetails.feedback.actual_impact_score,
                    prediction_error: eventDetails.feedback.prediction_error,
                    effectiveness_score: eventDetails.feedback.effectiveness_score,
                    observation_notes: eventDetails.feedback.observation_notes,
                    insight: null,
                    peer_accuracy: { sample_size: 0 },
                  }} />
                )}
              </section>
            )}

            {selectedEventId && eventDetails && (
              <div className="panel" style={{ padding: '8px 14px' }}>
                <button
                  className="btn secondary full-width"
                  onClick={() => {
                    const payload = {
                      event: eventDetails.event,
                      prediction: eventDetails.prediction,
                      forecast: eventForecast,
                      feedback: eventDetails.feedback,
                      exported_at: new Date().toISOString(),
                    };
                    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `gridlock-event-${selectedEventId.slice(0, 8)}.json`;
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                >
                  <ArrowUpRight size={14} />
                  Export Event Report (JSON)
                </button>
              </div>
            )}

            {/* Learning stats footer */}
            {dashboardSummary?.learning_stats?.total_outcomes_recorded > 0 && (
              <section className="panel" style={{ padding: '10px 14px' }}>
                <div className="panel-title" style={{ marginBottom: '6px' }}>
                  <BarChart3 size={14} />
                  <h2 style={{ fontSize: '12px' }}>System Learning</h2>
                </div>
                <div className="outcome-metrics" style={{ gap: '12px' }}>
                  <div>
                    <span className="label">Outcomes</span>
                    <span className="value">{dashboardSummary.learning_stats.total_outcomes_recorded}</span>
                  </div>
                  {dashboardSummary.learning_stats.mean_accuracy_pct != null && (
                    <div>
                      <span className="label">Avg Accuracy</span>
                      <span className="value" style={{ color: dashboardSummary.learning_stats.mean_accuracy_pct >= 70 ? '#22c55e' : '#eab308' }}>
                        {dashboardSummary.learning_stats.mean_accuracy_pct}%
                      </span>
                    </div>
                  )}
                  {dashboardSummary.learning_stats.mean_prediction_error != null && (
                    <div>
                      <span className="label">Avg Error</span>
                      <span className="value">±{dashboardSummary.learning_stats.mean_prediction_error}</span>
                    </div>
                  )}
                </div>
              </section>
            )}
          </>
        )}

        {activeTab === 'settings' && (
          <>
            <section className="panel">
              <div className="panel-title">
                <Clock size={17} />
                <h2>Temporal Filter</h2>
              </div>
              <div className="time-slider-container">
                <input
                  type="range"
                  min="-1"
                  max="23"
                  step="1"
                  value={localTime}
                  onChange={(e) => setLocalTime(Number(e.target.value))}
                  className="time-slider"
                />
                <button
                  className="btn primary full-width"
                  onClick={() => onParameterChange('time_hour', localTime === -1 ? null : localTime)}
                >
                  Apply Time
                </button>
              </div>
            </section>

            <section className="panel">
              <div className="panel-title">
                <Settings2 size={17} />
                <h2>Deployment Parameters</h2>
              </div>
              <div className="parameter-grid">
                <ParameterInput label="Patrol Cars" value={localParams.patrol_vehicles} min={1} max={6} onChange={v => setLocalParams(p => ({ ...p, patrol_vehicles: v }))} />
                <ParameterInput label="Max Stops" value={localParams.max_stops} min={1} max={12} onChange={v => setLocalParams(p => ({ ...p, max_stops: v }))} />
              </div>

              <button
                className="btn-link advanced-toggle"
                type="button"
                onClick={() => setShowAdvanced(v => !v)}
              >
                {showAdvanced ? '▾ Hide advanced' : '▸ Advanced clustering settings'}
              </button>

              {showAdvanced && (
                <div className="parameter-grid" style={{ marginTop: '6px' }}>
                  <ParameterInput label="Min Cluster" value={localParams.min_cluster_size} min={3} max={200} onChange={v => setLocalParams(p => ({ ...p, min_cluster_size: v }))} />
                  <ParameterInput label="Min Samples" value={localParams.min_samples} min={1} max={100} onChange={v => setLocalParams(p => ({ ...p, min_samples: v }))} />
                  <SelectInput
                    label="Clustering"
                    value={localParams.clustering_engine}
                    options={[{ value: 'hdbscan', label: 'HDBSCAN' }, { value: 'postgis', label: 'PostGIS ST_ClusterDBSCAN' }]}
                    onChange={v => setLocalParams(p => ({ ...p, clustering_engine: v }))}
                  />
                  <SelectInput
                    label="Routing"
                    value={localParams.routing_engine}
                    options={[{ value: 'pulp', label: 'PuLP (MILP)' }, { value: 'ortools', label: 'OR-Tools (VRP)' }]}
                    onChange={v => setLocalParams(p => ({ ...p, routing_engine: v }))}
                  />
                </div>
              )}

              <button
                className="btn primary full-width"
                style={{ marginTop: '8px' }}
                onClick={() => onApplyParameters(localParams)}
                disabled={loading}
              >
                Apply & Recalculate
              </button>
            </section>

            <section className="panel">
              <div className="panel-title">
                <AlertTriangle size={17} />
                <h2>Scenario Simulation</h2>
              </div>
              <p className="hint-text" style={{ textAlign: 'left', margin: '0 0 8px' }}>
                Synthetic what-if scenario — not live traffic data
              </p>

              <div
                className={`point-readout${selectedPoint ? ' has-point' : ''}`}
                onClick={() => setPlacementMode(true)}
                title="Click to place a point on the map"
              >
                <MapPin size={16} />
                <span>{selectedPoint ? `${selectedPoint[1].toFixed(4)}, ${selectedPoint[0].toFixed(4)}` : 'Click to pin location on map'}</span>
              </div>

              <div className="parameter-grid sim-matrix">
                <SelectInput
                  label="Vehicle"
                  value={selectedVehicle}
                  options={VEHICLE_OPTIONS}
                  onChange={setSelectedVehicle}
                />
                <SelectInput
                  label="Violation"
                  value={selectedViolation}
                  options={VIOLATION_OPTIONS}
                  onChange={setSelectedViolation}
                />
                <SelectInput
                  label="Scenario"
                  value={selectedEventType}
                  options={EVENT_TYPE_OPTIONS}
                  onChange={setSelectedEventType}
                />
              </div>

              <button className="btn danger full-width" onClick={onSimulate} disabled={loading || !selectedPoint}>
                <Play size={14} />
                Inject Synthetic Incident
              </button>
              {!selectedPoint && (
                <p className="hint-text">Pin a location on the map first</p>
              )}
            </section>

            <section className="panel performance-panel">
               <button className="btn secondary full-width" type="button" onClick={onSeedEvents} disabled={loading} style={{ marginBottom: '8px' }}>
                  <Calendar size={16} />
                  Seed Sample Events
                </button>
               <button className="btn secondary demo-btn full-width" type="button" onClick={onDemo} disabled={loading}>
                  <Play size={16} />
                  Demo: Tanker Spill Scenario
                </button>
               <p className="hint-text" style={{ textAlign: 'left', marginTop: '6px' }}>
                 For event demos: Events tab → select IPL Match, Political Rally, or Marathon
               </p>
            </section>
          </>
        )}
      </div>

      <section className="panel data-note">
        <Car size={16} />
        <span>{totalClusters} impact zones · {events.length} events · Bangalore</span>
        <span style={{ marginLeft: 'auto', fontSize: '10px', color: '#475569' }}>heuristic baseline model</span>
      </section>
    </aside>
  );
};

export default Sidebar;
