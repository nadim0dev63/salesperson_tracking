(function () {
    'use strict';

    if (!document.getElementById('startButton')) return;

    const root = document.getElementById('trackingRoot');
    const initialDist = root ? parseFloat(root.dataset.distance || '0') : 0;

    const state = {
        tracking: false,
        intervalId: null,
        lastPayload: null,
        trackingStart: null,
        timerId: null,
        lastLat: null,
        lastLng: null,
        totalDistance: initialDist,
    };

    const $ = (id) => document.getElementById(id);

    const el = {
        startButton: $('startButton'),
        stopButton: $('stopButton'),
        statusBadge: $('statusBadge'),
        statusDot: $('statusDot'),
        statusLabel: $('statusLabel'),
        noticeBox: $('noticeBox'),
        lastSeenValue: $('lastSeenValue'),
        latitudeValue: $('latitudeValue'),
        longitudeValue: $('longitudeValue'),
        locationNameValue: $('locationNameValue'),
        accuracyValue: $('accuracyValue'),
        takingTimeValue: $('takingTimeValue'),
        mapButton: $('mapButton'),
        kpiDistance: $('kpiDistance'),
    };

    const updateStatus = (status, label) => {
        const s = status || 'offline';
        el.statusBadge.className = `status-badge badge-${s}`;
        el.statusDot.className = `status-dot dot-${s}`;
        el.statusLabel.textContent = label || 'Offline';
    };

    const setNotice = (type, title, message) => {
        el.noticeBox.className = `notice${type ? ' notice-' + type : ''}`;
        el.noticeBox.innerHTML = `<strong>${title}</strong>${message}`;
    };

    const formatDuration = (ms) => {
        const totalSec = Math.floor(ms / 1000);
        const h = Math.floor(totalSec / 3600);
        const m = Math.floor((totalSec % 3600) / 60);
        const s = totalSec % 60;
        const pad = (n) => String(n).padStart(2, '0');

        return h > 0
            ? `${pad(h)}:${pad(m)}:${pad(s)}`
            : `${pad(m)}:${pad(s)}`;
    };

    const tickTimer = () => {
        if (!state.trackingStart) return;
        el.takingTimeValue.textContent = formatDuration(Date.now() - state.trackingStart);
    };

    const postJson = async (url, payload) => {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
            credentials: 'same-origin',
            keepalive: true,
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    };

    const getDistance = (lat1, lon1, lat2, lon2) => {
        const R = 6371;
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;

        const a =
            Math.sin(dLat / 2) ** 2 +
            Math.cos(lat1 * Math.PI / 180) *
            Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon / 2) ** 2;

        return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    };

    const refreshMetrics = (payload, resp) => {
        const lat = payload.latitude?.toFixed ? payload.latitude.toFixed(6) : payload.latitude;
        const lng = payload.longitude?.toFixed ? payload.longitude.toFixed(6) : payload.longitude;
        const acc = payload.accuracy ? Number(payload.accuracy).toFixed(1) : '—';

        el.latitudeValue.textContent = lat || '—';
        el.longitudeValue.textContent = lng || '—';
        el.locationNameValue.textContent = resp.location_name || '—';
        el.accuracyValue.textContent = acc !== '—' ? `${acc} m` : '—';
        el.lastSeenValue.textContent = resp.last_seen || new Date().toLocaleTimeString();

        if (resp.map_url) el.mapButton.href = resp.map_url;
        updateStatus(resp.status, resp.status_label);

        if (el.kpiDistance && resp.total_distance_km != null) {
            el.kpiDistance.textContent = parseFloat(resp.total_distance_km).toFixed(1);
        }
    };

    const saveStateToStorage = () => {
        localStorage.setItem('isTracking', 'true');
        localStorage.setItem('trackingStart', state.trackingStart);
        localStorage.setItem('totalDistance', state.totalDistance);
        if (state.lastLat !== null) localStorage.setItem('lastLat', state.lastLat);
        if (state.lastLng !== null) localStorage.setItem('lastLng', state.lastLng);
    };

    const clearStorage = () => {
        localStorage.removeItem('isTracking');
        localStorage.removeItem('trackingStart');
        localStorage.removeItem('totalDistance');
        localStorage.removeItem('lastLat');
        localStorage.removeItem('lastLng');
    };

    const GPS_OPTS = { enableHighAccuracy: true, maximumAge: 0, timeout: 30000 };
    const INTERVAL_MS = 2 * 1000;

    const fetchAndSend = () => {
        navigator.geolocation.getCurrentPosition(async (position) => {

            const lat = position.coords.latitude;
            const lng = position.coords.longitude;

            if (state.lastLat !== null && state.lastLng !== null) {
                state.totalDistance += getDistance(state.lastLat, state.lastLng, lat, lng);
            }

            state.lastLat = lat;
            state.lastLng = lng;

            // Persist updated distance & coords so reload doesn't lose them
            saveStateToStorage();

            const payload = {
                latitude: lat,
                longitude: lng,
                accuracy: position.coords.accuracy,
                speed: position.coords.speed,
                heading: position.coords.heading,
                source: 'browser',
                distance: state.totalDistance,
            };

            state.lastPayload = payload;
            console.log("GPS:", payload);

            try {
                const result = await postJson('/salesperson_tracking/update', payload);
                refreshMetrics(payload, result);
            } catch (e) {
                setNotice('warning', 'Update failed', e.message);
            }

        }, (err) => {
            setNotice('warning', 'GPS error', err.message || 'GPS error');
        }, GPS_OPTS);
    };

    const startTracking = async () => {
        if (!navigator.geolocation) {
            setNotice('danger', 'Not supported', 'Geolocation not supported');
            return;
        }

        if (state.tracking) return;

        state.tracking = true;
        el.startButton.disabled = true;
        el.stopButton.disabled = false;

        updateStatus('live', 'Starting...');
        state.trackingStart = Date.now();

        state.timerId = setInterval(tickTimer, 1000);

        saveStateToStorage();

        state.intervalId = setInterval(fetchAndSend, INTERVAL_MS);
        fetchAndSend();
    };

    const stopTracking = async () => {
        if (!state.tracking) return;

        state.tracking = false;

        if (state.intervalId) clearInterval(state.intervalId);
        if (state.timerId) clearInterval(state.timerId);

        state.intervalId = null;
        state.timerId = null;

        el.startButton.disabled = false;
        el.stopButton.disabled = true;

        const durationSeconds = state.trackingStart
            ? Math.floor((Date.now() - state.trackingStart) / 1000)
            : 0;

        state.trackingStart = null;
        state.lastLat = null;
        state.lastLng = null;
        state.totalDistance = initialDist;

        clearStorage();

        updateStatus('offline', 'Offline');
        setNotice('', 'Stopped', 'Tracking stopped');

        try {
            await postJson('/salesperson_tracking/stop', {
                duration_seconds: durationSeconds,
            });
        } catch (e) {
            console.warn('Stop request failed:', e.message);
        }
    };

    const autoResumeTracking = () => {
        if (localStorage.getItem('isTracking') !== 'true') return;

        const savedStart = parseInt(localStorage.getItem('trackingStart'));
        const savedDist  = parseFloat(localStorage.getItem('totalDistance') || '0');
        const savedLat   = localStorage.getItem('lastLat');
        const savedLng   = localStorage.getItem('lastLng');

        if (!savedStart || isNaN(savedStart)) {
            clearStorage();
            return;
        }

        state.tracking      = true;
        state.trackingStart = savedStart;
        state.totalDistance = isNaN(savedDist) ? initialDist : savedDist;
        state.lastLat       = savedLat !== null ? parseFloat(savedLat) : null;
        state.lastLng       = savedLng !== null ? parseFloat(savedLng) : null;

        el.startButton.disabled = true;
        el.stopButton.disabled  = false;

        updateStatus('live', 'Live');

        state.timerId    = setInterval(tickTimer, 1000);
        state.intervalId = setInterval(fetchAndSend, INTERVAL_MS);

        fetchAndSend();
    };

    el.startButton.addEventListener('click', startTracking);
    el.stopButton.addEventListener('click', stopTracking);

    window.addEventListener('load', autoResumeTracking);

})();