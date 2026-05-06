(function () {
    'use strict';
    var mapEl = document.getElementById('map');
    if (!mapEl) return;
    var b64     = mapEl.getAttribute('data-points') || '';
    var planB64 = mapEl.getAttribute('data-plans')  || '';
    var points  = [];
    var plans   = [];

    try { points = JSON.parse(atob(b64));     } catch (e) { points = []; }
    try { plans  = JSON.parse(atob(planB64)); } catch (e) { plans  = []; }

    var DEFAULT_CENTER = [23.7701, 90.4254];
    var map = L.map('map', { zoomControl: true }).setView(DEFAULT_CENTER, 15);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '\u00a9 <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
    }).addTo(map);
    
    if (!points || points.length === 0) {
        document.getElementById('routeLoading').style.display = 'none';

        var nd = document.createElement('div');
        nd.className = 'no-data';
        nd.innerHTML = [
            '<div class="no-data-card">',
            '  <div class="no-data-icon">',
            '    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">',
            '      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z"/>',
            '      <circle cx="12" cy="10" r="3"/>',
            '    </svg>',
            '  </div>',
            '  <div class="no-data-title">No location data</div>',
            '  <div class="no-data-sub">No GPS logs available for today.</div>',
            '</div>',
        ].join('');
        document.querySelector('.map-wrapper').appendChild(nd);
        return;
    }

    var valid = points.filter(function (p) {
        return typeof p.lat === 'number'
            && typeof p.lng === 'number'
            && (p.accuracy <= 200 || p.accuracy === 0);
    });
    if (valid.length === 0) valid = points;

    var latlngs = valid.map(function (p) { return [p.lat, p.lng]; });

  
    function haversineKm(a, b) {
        var R = 6371;
        var dLat = (b[0] - a[0]) * Math.PI / 180;
        var dLng = (b[1] - a[1]) * Math.PI / 180;
        var sin1 = Math.sin(dLat / 2);
        var sin2 = Math.sin(dLng / 2);
        var x = sin1 * sin1 +
                Math.cos(a[0] * Math.PI / 180) *
                Math.cos(b[0] * Math.PI / 180) *
                sin2 * sin2;
        return R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
    }

    function totalDistanceKm(pts) {
        var d = 0;
        for (var i = 1; i < pts.length; i++) d += haversineKm(pts[i - 1], pts[i]);
        return d.toFixed(1);
    }

    function addStartMarker(p) {
        return L.marker([p.lat, p.lng], {
            icon: L.divIcon({
                html: '<div style="width:16px;height:16px;background:#3b6d11;border-radius:50%;border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.3)"></div>',
                iconSize:   [16, 16],
                iconAnchor: [8, 8],
                className:  '',
            }),
        })
        .addTo(map)
        .bindPopup(
            '<b>Start point</b><br>' + (p.time || '') +
            (p.location_name ? '<br>' + p.location_name : '')
        );
    }

    function addEndMarker(p) {
        var heading = (p.heading !== null && p.heading !== undefined && !isNaN(p.heading))
            ? p.heading : 0;

        var html = [
            '<div style="position:relative;width:44px;height:44px;">',
            '<div style="',
            'position:absolute;top:0;left:50%;transform:translateX(-50%) rotate(' + heading + 'deg);',
            'width:0;height:0;',
            'border-left:7px solid transparent;',
            'border-right:7px solid transparent;',
            'border-bottom:16px solid #dc2626;',
            '"></div>',
            '<div style="',
            'position:absolute;bottom:0;left:50%;transform:translateX(-50%);',
            'width:32px;height:32px;',
            'background:#dc2626;',
            'border-radius:50%;',
            'border:3px solid #fff;',
            'box-shadow:0 2px 10px rgba(0,0,0,.35);',
            'display:flex;align-items:center;justify-content:center;',
            'font-size:17px;line-height:1;',
            '">&#x1F6B6;</div>',
            '</div>',
        ].join('');

        return L.marker([p.lat, p.lng], {
            icon: L.divIcon({
                html:       html,
                iconSize:   [44, 44],
                iconAnchor: [22, 44],
                className:  '',
            }),
        })
        .addTo(map)
        .bindPopup(
            '<b>Current / last position</b><br>' + (p.time || '') +
            (p.location_name ? '<br>' + p.location_name : '') +
            '<br>Accuracy: ' + (p.accuracy ? p.accuracy.toFixed(1) + ' m' : '—') +
            (heading ? '<br>Heading: ' + Math.round(heading) + '\u00b0' : '')
        )
        .openPopup();
    }

    function addIntermediateMarkers(pts) {
        pts.forEach(function (p, i) {
            if (i === 0 || i === pts.length - 1) return;

            if (p.accuracy > 0 && p.accuracy <= 500) {
                L.circle([p.lat, p.lng], {
                    radius:      p.accuracy,
                    color:       '#3b82f6',
                    fillColor:   '#3b82f6',
                    fillOpacity: 0.08,
                    weight:      1,
                    opacity:     0.3,
                }).addTo(map);
            }

            var dot = L.circleMarker([p.lat, p.lng], {
                radius:      4,
                color:       '#1a73e8',
                fillColor:   '#bfdbfe',
                fillOpacity: 1,
                weight:      2,
            });

            var timeStr  = p.time  ? p.time.replace('T', ' ') : '—';
            var speedStr = p.speed ? (p.speed * 3.6).toFixed(1) + ' km/h' : '0 km/h';
            var accStr   = p.accuracy ? p.accuracy.toFixed(1) + ' m' : '—';

            dot.bindPopup(
                '<b>Time:</b> '     + timeStr  + '<br>' +
                '<b>Speed:</b> '    + speedStr + '<br>' +
                '<b>Accuracy:</b> ' + accStr   +
                (p.location_name ? '<br><b>Location:</b> ' + p.location_name : '')
            );
            dot.addTo(map);
        });
    }

    function addPlanMarkers(planList) {
        if (!planList || planList.length === 0) return;
        planList.forEach(function (pl) {
            if (typeof pl.lat !== 'number' || typeof pl.lng !== 'number') return;
            var visited = !!pl.visited;
            var color   = visited ? '#3b6d11' : '#dc2626';
            L.circleMarker([pl.lat, pl.lng], {
                radius:      7,
                color:       color,
                fillColor:   color,
                fillOpacity: visited ? 0.55 : 0.45,
                weight:      2,
            })
            .bindPopup(
                '<b>' + (pl.name || 'Planned location') + '</b><br>' +
                (visited ? '&#10003; Visited' : '&#8226; Not visited yet') +
                (pl.address ? '<br>' + pl.address : '')
            )
            .addTo(map);
        });
    }

 
    function fitAll(bounds) {
        var allLatLngs = latlngs.slice();
        if (plans) {
            plans.forEach(function (pl) {
                if (typeof pl.lat === 'number') allLatLngs.push([pl.lat, pl.lng]);
            });
        }
        if (bounds) {
            map.fitBounds(bounds, { padding: [50, 50] });
        } else if (allLatLngs.length > 1) {
            map.fitBounds(L.latLngBounds(allLatLngs), { padding: [50, 50] });
        } else {
            map.setView(latlngs[0], 16);
        }
        setTimeout(function () { map.invalidateSize(); }, 300);
    }

 

    function showRouteInfo(distKm) {
        var box = document.getElementById('routeInfoBox');
        var dp  = document.getElementById('routeDistancePill');
        var dv  = document.getElementById('routeDistVal');

        document.getElementById('ribDur').style.display  = 'none'; // no ETA for GPS path
        document.getElementById('ribDist').textContent   = distKm + ' km';
        if (box) box.style.display = 'block';

        if (dp) { dp.style.display = 'flex'; dv.textContent = distKm + ' km'; }
    }

    

    function drawExactGpsPath() {
        /* white outline for legibility on the tile layer */
        L.polyline(latlngs, {
            color:   '#ffffff',
            weight:  9,
            opacity: 0.5,
        }).addTo(map);

        var line = L.polyline(latlngs, {
            color:   '#1a73e8',
            weight:  5,
            opacity: 0.9,
        }).addTo(map);

        return line;
    }


    function finishRender(bounds) {
        addIntermediateMarkers(valid);
        addStartMarker(valid[0]);
        if (valid.length > 1) addEndMarker(valid[valid.length - 1]);
        addPlanMarkers(plans);
        fitAll(bounds || null);
    }

  
    var loadingEl = document.getElementById('routeLoading');
    if (loadingEl) loadingEl.style.display = 'none'; // no async loading needed

    if (valid.length === 1) {
        addEndMarker(valid[0]);
        addPlanMarkers(plans);
        map.setView([valid[0].lat, valid[0].lng], 16);
        setTimeout(function () { map.invalidateSize(); }, 300);
        return;
    }

    /* Draw exact GPS polyline for ALL point counts (≥ 2) */
    var gpxLine = drawExactGpsPath();
    var distKm  = totalDistanceKm(latlngs);
    showRouteInfo(distKm);
    finishRender(gpxLine.getBounds());

})();