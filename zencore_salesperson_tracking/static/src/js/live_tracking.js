(function () {
    'use strict';

    const root = document.getElementById('trackingRoot');
    if (!root) return;

    const state = {
        tracking: false,
        intervalId: null,
        trackingStart: null,
        timerId: null,
        lastLat: null,
        lastLng: null,
        totalDistance: parseFloat(root.dataset.distance || '0'),
        consecutiveErrors: 0,
    };

    const serverTracking = root.dataset.isTracking === '1';
    const isOwner        = root.dataset.isOwner === '1';
    const trackerId      = root.dataset.trackerId;

    const $ = id => document.getElementById(id);

    // ── Leaflet map (shared by owner + manager) ───────────────────────────
    let liveMap      = null;
    let liveMarker   = null;
    let livePolyline = null;
    let livePath     = [];   // [[lat,lng], ...]

    function initLiveMap() {
        if (liveMap || typeof L === 'undefined') return;
        const el = $('liveMap');
        if (!el) return;
        liveMap = L.map('liveMap', { zoomControl: true }).setView([23.7701, 90.4254], 14);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
        }).addTo(liveMap);
    }

    function pushToMap(lat, lng) {
        if (!liveMap) initLiveMap();
        if (!liveMap) return;

        livePath.push([lat, lng]);

        if (livePolyline) {
            livePolyline.addLatLng([lat, lng]);
        } else {
            livePolyline = L.polyline(livePath, { color: '#22c55e', weight: 4 }).addTo(liveMap);
        }

        if (!liveMarker) {
            liveMarker = L.circleMarker([lat, lng], {
                radius: 10, color: '#fff', fillColor: '#ef4444', fillOpacity: 1, weight: 3,
            }).addTo(liveMap);
        } else {
            liveMarker.setLatLng([lat, lng]);
        }

        liveMap.panTo([lat, lng], { animate: true, duration: 0.4 });
        setTimeout(() => liveMap.invalidateSize(), 100);
    }

    // ── Helpers ───────────────────────────────────────────────────────────
    const formatDuration = ms => {
        const s = Math.floor(ms / 1000);
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const sec = s % 60;
        return h > 0
            ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`
            : `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
    };

    const updateStatus = (status, label) => {
        const badge = $('statusBadge'), dot = $('statusDot'), lbl = $('statusLabel');
        if (badge) badge.className   = `status-badge badge-${status}`;
        if (dot)   dot.className     = `status-dot dot-${status}`;
        if (lbl)   lbl.textContent   = label || status;
    };

    const setNotice = (title, msg, isErr = false) => {
        const n = $('noticeBox');
        if (n) {
            n.innerHTML = `<strong>${title}</strong> ${msg}`;
            n.style.background = isErr ? '#fee2e2' : '#fef9e6';
        }
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

    const haversine = (lat1, lon1, lat2, lon2) => {
        const R = 6371;
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat/2)**2
                + Math.cos(lat1 * Math.PI/180) * Math.cos(lat2 * Math.PI/180)
                * Math.sin(dLon/2)**2;
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    };

    const setStartBtn = disabled => {
        const b = $('startButton'); if (!b) return;
        b.disabled = disabled;
        b.style.opacity = disabled ? '0.45' : '1';
        b.style.cursor  = disabled ? 'not-allowed' : 'pointer';
    };
    const setStopBtn = disabled => {
        const b = $('stopButton'); if (!b) return;
        b.disabled = disabled;
        b.style.opacity = disabled ? '0.45' : '1';
        b.style.cursor  = disabled ? 'not-allowed' : 'pointer';
    };

    // ── Error recovery ────────────────────────────────────────────────────
    // Never reload the page — a reload stops GPS tracking silently.
    // Instead we use exponential back-off: pause the send interval for a
    // few seconds on repeated errors, then resume automatically.
    const handleError = msg => {
        state.consecutiveErrors++;
        setNotice('Update Failed', `${msg} (${state.consecutiveErrors} consecutive)`, true);

        const BACKOFF_AT    = [3, 6, 10];
        const BACKOFF_SECS  = [5, 10, 30];
        const idx = BACKOFF_AT.indexOf(state.consecutiveErrors);
        if (idx !== -1) {
            const secs = BACKOFF_SECS[idx];
            setNotice('Retrying\u2026', `Pausing GPS for ${secs}s then resuming.`, true);
            clearInterval(state.intervalId);
            state.intervalId = null;
            setTimeout(() => {
                if (state.tracking) {
                    state.intervalId = setInterval(fetchAndSend, 2000);
                    setNotice('Tracking Active', 'Location updates every 2 seconds.');
                }
            }, secs * 1000);
        }
    };

    // ── GPS push (owner/salesperson only) ─────────────────────────────────
    const fetchAndSend = () => {
        if (!state.tracking) return; // Don't send if not tracking
        
        navigator.geolocation.getCurrentPosition(async pos => {
            const lat = pos.coords.latitude;
            const lng = pos.coords.longitude;

            if (state.lastLat !== null)
                state.totalDistance += haversine(state.lastLat, state.lastLng, lat, lng);
            state.lastLat = lat;
            state.lastLng = lng;

            const payload = {
                latitude:  lat,
                longitude: lng,
                accuracy:  pos.coords.accuracy,
                distance:  state.totalDistance,
                tracker_id: trackerId, // Always send tracker_id
            };

            try {
                const result = await postJson('/salesperson_tracking/update', payload);
                state.consecutiveErrors = 0;

                // Update UI
                if ($('latitudeValue'))     $('latitudeValue').textContent     = lat.toFixed(6);
                if ($('longitudeValue'))    $('longitudeValue').textContent    = lng.toFixed(6);
                if ($('locationNameValue')) $('locationNameValue').textContent = result.location_name || '—';
                if ($('accuracyValue'))     $('accuracyValue').textContent     = pos.coords.accuracy ? `${pos.coords.accuracy.toFixed(1)} m` : '—';
                if ($('lastSeenValue'))     $('lastSeenValue').textContent     = result.last_seen || new Date().toLocaleTimeString();
                if ($('kpiDistance') && result.total_distance_km != null)
                    $('kpiDistance').textContent = parseFloat(result.total_distance_km).toFixed(1);

                updateStatus(result.status, result.status_label);
                pushToMap(lat, lng);
            } catch(e) {
                console.error('Update error:', e);
                handleError(e.message);
            }
        }, err => {
            console.error('Geolocation error:', err);
            let errorMsg = err.message;
            if (err.code === 1) errorMsg = 'Location access denied. Please enable GPS.';
            else if (err.code === 2) errorMsg = 'GPS position unavailable. Check your location.';
            else if (err.code === 3) errorMsg = 'GPS timed out. Retrying...';
            handleError(errorMsg);
        }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
    };

    const tickTimer = () => {
        const el = $('takingTimeValue');
        if (el && state.trackingStart) el.textContent = formatDuration(Date.now() - state.trackingStart);
    };

    const startTracking = async () => {
        if (!navigator.geolocation) { setNotice('Not Supported', 'Geolocation unavailable', true); return; }
        if (state.tracking) return;
        state.tracking = true;
        state.consecutiveErrors = 0;
        setStartBtn(true);
        setStopBtn(false);
        updateStatus('live', 'Live');
        initLiveMap();
        state.trackingStart = Date.now();
        state.timerId    = setInterval(tickTimer, 1000);
        state.intervalId = setInterval(fetchAndSend, 2000);
        fetchAndSend();
        try { await postJson('/salesperson_tracking/start', { tracker_id: trackerId }); } catch(e) {}
        setNotice('Tracking Active', 'Location updates every 2 seconds.');
    };

    const stopTracking = async () => {
        if (!state.tracking) return;
        state.tracking = false;
        state.consecutiveErrors = 0;
        clearInterval(state.intervalId);
        clearInterval(state.timerId);
        state.intervalId = state.timerId = null;
        state.trackingStart = state.lastLat = state.lastLng = null;
        setStartBtn(false);
        setStopBtn(true);
        updateStatus('offline', 'Offline');
        setNotice('Tracking Stopped', 'Location updates have been stopped.');
        try { await postJson('/salesperson_tracking/stop', { tracker_id: trackerId }); } catch(e) {}
    };

    // ── Manager / viewer polling ──────────────────────────────────────────
    // Uses cursor-based incremental fetching (?after_id=N) so each poll only
    // fetches NEW points instead of all logs from today.
    // The loop is self-healing: it always reschedules itself even after errors,
    // so it never silently dies during a long tracking session.
    let managerLastId   = 0;          // cursor — id of last received log
    let managerPollErrors = 0;

    const pollManagerView = async () => {
        try {
            const url = '/salesperson_tracking/moving_map_data/' + trackerId
                      + (managerLastId ? '?after_id=' + managerLastId : '');
            const res  = await fetch(url);
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            if (!data.ok) return;

            managerPollErrors = 0;

            updateStatus(data.status, data.status_label);

            if (data.last_seen && $('lastSeenValue'))
                $('lastSeenValue').textContent = data.last_seen;

            if ($('kpiDistance') && data.total_distance_km != null)
                $('kpiDistance').textContent = parseFloat(data.total_distance_km).toFixed(1);

            const newPts = data.points || [];
            if (!newPts.length) return;

            // Advance the cursor so the next poll only fetches rows after this
            if (data.last_id && data.last_id > managerLastId)
                managerLastId = data.last_id;

            if (!liveMap) initLiveMap();

            if (managerLastId === (data.last_id || 0) && livePath.length === 0) {
                // First load: build the full path from initial points
                livePath = newPts.map(p => [p.lat, p.lng]);
                if (livePolyline) { liveMap.removeLayer(livePolyline); livePolyline = null; }
                if (livePath.length > 1)
                    livePolyline = L.polyline(livePath, { color: '#22c55e', weight: 4 }).addTo(liveMap);
            } else {
                // Incremental: append only new points to the existing polyline
                for (const p of newPts) {
                    livePath.push([p.lat, p.lng]);
                    if (livePolyline) {
                        livePolyline.addLatLng([p.lat, p.lng]);
                    } else {
                        livePolyline = L.polyline(livePath, { color: '#22c55e', weight: 4 }).addTo(liveMap);
                    }
                }
            }

            const last = newPts[newPts.length - 1];
            if (liveMarker) {
                liveMarker.setLatLng([last.lat, last.lng]);
            } else {
                liveMarker = L.circleMarker([last.lat, last.lng], {
                    radius: 10, color: '#fff', fillColor: '#ef4444', fillOpacity: 1, weight: 3,
                }).bindTooltip(data.last_seen || '').addTo(liveMap);
            }
            liveMap.panTo([last.lat, last.lng], { animate: true, duration: 0.5 });

            if ($('latitudeValue'))  $('latitudeValue').textContent  = parseFloat(last.lat).toFixed(6);
            if ($('longitudeValue')) $('longitudeValue').textContent = parseFloat(last.lng).toFixed(6);

            setTimeout(() => liveMap.invalidateSize(), 100);
        } catch(e) {
            managerPollErrors++;
            console.warn('[LiveTracking] pollManagerView error #' + managerPollErrors + ':', e);
            // Back off briefly on repeated errors but ALWAYS reschedule
        }
    };

    // Self-healing scheduler — always reschedules even after errors
    const scheduleManagerPoll = () => {
        const delay = managerPollErrors > 5 ? 10000 : 3000;
        setTimeout(async () => {
            await pollManagerView();
            scheduleManagerPoll();
        }, delay);
    };

    // ── Boot ──────────────────────────────────────────────────────────────
    initLiveMap();

    if (isOwner) {
        // Salesperson: GPS controls
        const startBtn = $('startButton');
        const stopBtn  = $('stopButton');
        if (startBtn) startBtn.addEventListener('click', startTracking);
        if (stopBtn)  stopBtn.addEventListener('click', stopTracking);

        if (serverTracking) {
            setStartBtn(true); setStopBtn(false);
            startTracking();
        } else {
            setStartBtn(false); setStopBtn(true);
        }
    } else {
        // Manager/viewer: start incremental polling immediately, then loop forever
        pollManagerView().then(scheduleManagerPoll);
    }

})();
