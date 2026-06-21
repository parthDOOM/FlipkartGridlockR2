import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import MapComponent from './components/Map';
import Sidebar from './components/Sidebar';
import './App.css';

// Empty string = same-origin (production). Explicit value = local dev or separate deployment.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

const DEFAULT_PARAMETERS = {
  min_cluster_size: 15,
  min_samples: 5,
  patrol_vehicles: 2,
  max_stops: 5,
  candidate_limit: 18,
  distance_penalty: 14,
  map_cluster_limit: 300,
  route_geometry: 'road',
  solver_time_limit: 5,
  time_hour: null,
  clustering_engine: 'hdbscan',
  routing_engine: 'pulp',
};

function App() {
  const [data, setData] = useState({ clusters: [], routes: [], summary: {}, metrics: {} });
  const [events, setEvents] = useState([]);
  const [selectedEventId, setSelectedEventId] = useState(null);
  const [eventDetails, setEventDetails] = useState(null);
  const [parameters, setParameters] = useState(DEFAULT_PARAMETERS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [placementMode, setPlacementMode] = useState(false);
  const [selectedPoint, setSelectedPoint] = useState(null);
  const [selectedVehicle, setSelectedVehicle] = useState('TANKER');
  const [selectedViolation, setSelectedViolation] = useState('WRONG PARKING');
  const [selectedEventType, setSelectedEventType] = useState('SINGLE');
  const [lastUpdated, setLastUpdated] = useState(null);
  const [demoActive, setDemoActive] = useState(false);
  const [eventForecast, setEventForecast] = useState(null);
  const [eventLearning, setEventLearning] = useState(null);
  const [dashboardSummary, setDashboardSummary] = useState(null);
  const [liveIncidents, setLiveIncidents] = useState(null);

  const abortControllerRef = useRef(null);

  const requestParams = useMemo(() => {
    const params = { ...parameters };
    if (params.time_hour === null) delete params.time_hour;
    return params;
  }, [parameters]);

  const fetchEvents = useCallback(async () => {
    try {
      const response = await axios.get(`${API_BASE_URL}/api/v1/events`);
      setEvents(response.data);
    } catch (e) {
      console.error('Failed to fetch events', e);
    }
  }, []);

  const fetchEventDetails = useCallback(async (id) => {
    if (!id) return;
    try {
      const [detailRes, forecastRes, learningRes] = await Promise.allSettled([
        axios.get(`${API_BASE_URL}/api/v1/events/${id}`),
        axios.get(`${API_BASE_URL}/api/v1/events/${id}/forecast`),
        axios.get(`${API_BASE_URL}/api/v1/events/${id}/learning`),
      ]);
      if (detailRes.status === 'fulfilled') setEventDetails(detailRes.value.data);
      if (forecastRes.status === 'fulfilled') setEventForecast(forecastRes.value.data);
      else setEventForecast(null);
      if (learningRes.status === 'fulfilled') setEventLearning(learningRes.value.data);
      else setEventLearning(null);
    } catch (e) {
      console.error('Failed to fetch event details', e);
    }
  }, []);

  const fetchDashboardSummary = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE_URL}/api/v1/dashboard-summary`);
      setDashboardSummary(res.data);
    } catch (e) {
      console.error('Failed to fetch dashboard summary', e);
    }
  }, []);

  const fetchLiveIncidents = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE_URL}/api/v1/live-incidents`);
      setLiveIncidents(res.data);
    } catch (e) {
      console.error('Failed to fetch live incidents', e);
    }
  }, []);

  useEffect(() => {
    if (selectedEventId) {
      fetchEventDetails(selectedEventId);
    } else {
      setEventDetails(null);
      setEventForecast(null);
      setEventLearning(null);
    }
  }, [selectedEventId, fetchEventDetails]);

  const fetchZones = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();

    setLoading(true);
    setError('');
    try {
      const response = await axios.get(`${API_BASE_URL}/api/v1/congestion-zones`, {
        params: requestParams,
        signal: abortControllerRef.current.signal,
      });
      setData(response.data);
      setLastUpdated(new Date());
      fetchEvents();
      fetchDashboardSummary();
    } catch (requestError) {
      if (axios.isCancel(requestError)) return;
      setError(requestError.response?.data?.detail || requestError.message || 'Unable to load congestion zones.');
    } finally {
      setLoading(false);
    }
  }, [requestParams, fetchEvents, fetchDashboardSummary]);

  const runDemoSequence = async () => {
    setDemoActive(true);
    setParameters(DEFAULT_PARAMETERS);
    
    // Step 1: Initial load
    await fetchZones();
    await new Promise(r => setTimeout(r, 2000));
    
    // Step 2: Tanker insertion (centered on Bangalore HQ)
    const [lon, lat] = [77.5994, 12.9784];
    setSelectedPoint([lon, lat]);
    setSelectedEventType('TANKER_SPILL');
    await new Promise(r => setTimeout(r, 1500));
    
    // Step 3: Simulate
    setLoading(true);
    try {
      const response = await axios.post(
        `${API_BASE_URL}/api/v1/simulate-anomaly`,
        {
          latitude: lat,
          longitude: lon,
          vehicle_type: 'TANKER',
          violation_type: 'WRONG PARKING',
          event_type: 'TANKER_SPILL',
        },
        { params: DEFAULT_PARAMETERS },
      );
      setData(response.data);
      setSelectedPoint(null);
      setLastUpdated(new Date());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
      setDemoActive(false);
    }
  };

  useEffect(() => {
    fetchZones();
  }, [fetchZones]);

  useEffect(() => {
    fetchLiveIncidents();
    const id = setInterval(fetchLiveIncidents, 60_000);
    return () => clearInterval(id);
  }, [fetchLiveIncidents]);

  const STRING_PARAMS = new Set(['route_geometry', 'clustering_engine', 'routing_engine']);

  const updateParameter = (key, value) => {
    setParameters((current) => ({
      ...current,
      [key]: STRING_PARAMS.has(key) ? value : (value === null ? null : Number(value)),
    }));
  };

  const applyParameters = (patch) => {
    setParameters((current) => ({
      ...current,
      ...Object.fromEntries(
        Object.entries(patch).map(([k, v]) => [k, STRING_PARAMS.has(k) ? v : (v === null ? null : Number(v))])
      ),
    }));
  };

  const handleMapClick = useCallback((info) => {
    if (info.object && info.object.event_type) {
        setSelectedEventId(info.object.id);
        return;
    }
    
    if (!placementMode || !info.coordinate) return;

    const [longitude, latitude] = info.coordinate;
    setSelectedPoint([Number(longitude.toFixed(6)), Number(latitude.toFixed(6))]);
  }, [placementMode]);

  const simulateAnomaly = async () => {
    if (!selectedPoint) {
      setPlacementMode(true);
      return;
    }

    setLoading(true);
    setError('');
    try {
      const [longitude, latitude] = selectedPoint;
      const response = await axios.post(
        `${API_BASE_URL}/api/v1/simulate-anomaly`,
        {
          latitude,
          longitude,
          vehicle_type: selectedVehicle,
          violation_type: selectedViolation,
          event_type: selectedEventType,
        },
        { params: requestParams },
      );

      setData(response.data);
      setPlacementMode(false);
      setSelectedPoint(null);
      setLastUpdated(new Date());
    } catch (requestError) {
      setError(requestError.response?.data?.detail || requestError.message || 'Unable to simulate anomaly.');
    } finally {
      setLoading(false);
    }
  };

  const submitFeedback = async (id, feedback) => {
    try {
        await axios.post(`${API_BASE_URL}/api/v1/events/${id}/feedback`, feedback);
        fetchEventDetails(id);
        fetchDashboardSummary();
    } catch (e) {
        console.error('Failed to submit feedback', e);
    }
  };

  const seedEvents = async () => {
    setLoading(true);
    try {
      await axios.post(`${API_BASE_URL}/api/v1/seed-events`);
      await fetchEvents();
      fetchDashboardSummary();
    } catch (e) {
      console.error('Failed to seed events', e);
      setError('Failed to seed events');
    } finally {
      setLoading(false);
    }
  };

  const activateEvent = async (id) => {
    setLoading(true);
    setError('');
    try {
      const response = await axios.post(
        `${API_BASE_URL}/api/v1/events/${id}/activate`,
        null,
        { params: { patrol_vehicles: parameters.patrol_vehicles, max_stops: parameters.max_stops, routing_engine: parameters.routing_engine } },
      );
      setData(response.data);
      setLastUpdated(new Date());
      fetchEvents();
      fetchDashboardSummary();
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to activate event.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-shell">
      <Sidebar
        data={data}
        events={events}
        selectedEventId={selectedEventId}
        setSelectedEventId={setSelectedEventId}
        eventDetails={eventDetails}
        eventForecast={eventForecast}
        dashboardSummary={dashboardSummary}
        liveIncidents={liveIncidents}
        parameters={parameters}
        onParameterChange={updateParameter}
        onApplyParameters={applyParameters}
        placementMode={placementMode}
        setPlacementMode={setPlacementMode}
        selectedPoint={selectedPoint}
        setSelectedPoint={setSelectedPoint}
        selectedVehicle={selectedVehicle}
        setSelectedVehicle={setSelectedVehicle}
        selectedViolation={selectedViolation}
        setSelectedViolation={setSelectedViolation}
        selectedEventType={selectedEventType}
        setSelectedEventType={setSelectedEventType}
        onSimulate={simulateAnomaly}
        eventLearning={eventLearning}
        onFeedback={submitFeedback}
        onActivateEvent={activateEvent}
        onSeedEvents={seedEvents}
        onDemo={runDemoSequence}
        onRefresh={fetchZones}
        loading={loading || demoActive}
        lastUpdated={lastUpdated}
      />

      <main className="map-stage">
        {loading && (
          <div className="loading-overlay">
            <div className="spinner" />
            <span>
              Running {parameters.clustering_engine === 'postgis' ? 'PostGIS' : 'HDBSCAN'}, impact scoring, and {parameters.routing_engine === 'ortools' ? 'OR-Tools' : 'PuLP'} routing
            </span>
          </div>
        )}

        {error && (
          <div className="error-toast">
            {error}
          </div>
        )}

        <MapComponent
          clusters={data.clusters || []}
          events={events}
          selectedEventId={selectedEventId}
          eventDetails={eventDetails}
          routes={data.routes || []}
          summary={data.summary || {}}
          metrics={data.metrics || {}}
          selectedPoint={selectedPoint}
          onMapClick={handleMapClick}
          interactiveCursor={placementMode ? 'crosshair' : 'grab'}
        />
      </main>
    </div>
  );
}

export default App;
