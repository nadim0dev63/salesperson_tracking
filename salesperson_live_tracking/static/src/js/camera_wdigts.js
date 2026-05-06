(function () {
    'use strict';
    function init() {
        const openBtn      = document.getElementById('openCameraBtn');
        if (!openBtn) return;
        if (openBtn.dataset.cameraInit) return;   // ← already initialised, skip
        openBtn.dataset.cameraInit = '1';

        const video        = document.getElementById('selfieVideo');
        const canvas       = document.getElementById('selfieCanvas');
        const previewBox   = document.getElementById('previewBox');
        const snapRow      = document.getElementById('snapRow');
        const camLabel     = document.getElementById('camLabel');
        const stopBtn      = document.getElementById('stopBtn');
        const captureBtn   = document.getElementById('captureBtn');
        const flipBtn      = document.getElementById('flipBtn');
        const flashEl      = document.getElementById('flashEl');
        const downloadLink = document.getElementById('downloadLink');
        const camGallery   = document.getElementById('camGallery');
        const galleryGrid  = document.getElementById('galleryGrid');
        const galleryCount = document.getElementById('galleryCount');
        const clearAllBtn  = document.getElementById('clearAllBtn');
        const photoViewer  = document.getElementById('photoViewer');
        const viewerImg    = document.getElementById('viewerImg');
        const pvBack       = document.getElementById('pvBack');
        const pvDownload   = document.getElementById('pvDownload');
        const pvDelete     = document.getElementById('pvDelete');

        let stream       = null;
        let facingMode   = 'environment';
        let photos       = [];
        let viewingIndex = -1;
        let isUploading  = false;   // ← guard: prevent double-upload
        function isSecureContext() {
            return (
                window.isSecureContext === true ||               
                location.protocol === 'https:' ||
                location.hostname === 'localhost' ||
                location.hostname === '127.0.0.1' ||
                location.hostname === '[::1]'                  
            );
        }

        async function startCamera(facing) {

            if (!isSecureContext()) {
                camLabel.textContent = ' Camera requires HTTPS or localhost. Please use a secure connection.';
                return;
            }

            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                camLabel.textContent = ' Your browser does not support camera access.';
                return;
            }

            if (stream) {
                stream.getTracks().forEach((t) => t.stop());
                stream = null;
            }

            camLabel.textContent = 'Opening camera…';

            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: { ideal: facing },
                        width:      { ideal: 1280 },
                        height:     { ideal: 960 },
                    },
                    audio: false,
                });

                video.srcObject           = stream;
                video.style.display       = 'block';
                previewBox.style.display  = 'block';
                openBtn.style.display     = 'none';
                snapRow.style.display     = 'none';
                camGallery.style.display  = 'none';
                photoViewer.style.display = 'none';
                camLabel.textContent      = 'Tap the shutter button to take a photo';
                video.play().catch(() => {});

            } catch (e) {
                console.error('Camera error:', e);

                if (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError') {
                    camLabel.textContent = ' Camera permission denied. Please allow camera access in your browser settings.';
                } else if (e.name === 'NotFoundError' || e.name === 'DevicesNotFoundError') {
                    camLabel.textContent = ' No camera found on this device.';
                } else if (e.name === 'NotReadableError' || e.name === 'TrackStartError') {
                    camLabel.textContent = ' Camera is in use by another app. Please close it and try again.';
                } else if (e.name === 'OverconstrainedError') {
                 
                    try {
                        stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
                        video.srcObject          = stream;
                        video.style.display      = 'block';
                        previewBox.style.display = 'block';
                        openBtn.style.display    = 'none';
                        snapRow.style.display    = 'none';
                        camLabel.textContent     = 'Tap the shutter button to take a photo';
                        video.play().catch(() => {});
                    } catch (e2) {
                        camLabel.textContent = ' Could not start camera: ' + e2.message;
                    }
                } else {
                    camLabel.textContent = ' Camera error: ' + (e.message || e.name);
                }
            }
        }

        /* ══════════════════════════════════════════════
           CAMERA — close
        ══════════════════════════════════════════════ */
        function closeCamera() {
            if (stream) {
                stream.getTracks().forEach((t) => t.stop());
                stream = null;
            }
            video.srcObject          = null;
            video.style.display      = 'none';
            previewBox.style.display = 'none';
            openBtn.style.display    = 'inline-flex';
            camLabel.textContent     = 'Click to access your camera';
            snapRow.style.display    = 'none';
            if (photos.length > 0) renderGallery();
        }

        /* ══════════════════════════════════════════════
           GALLERY
        ══════════════════════════════════════════════ */
        function renderGallery() {
            camGallery.style.display = 'flex';
            galleryCount.textContent = 'Saved photos (' + photos.length + ')';
            galleryGrid.innerHTML    = '';

            if (photos.length === 0) {
                galleryGrid.innerHTML = '<div class="gallery-empty">No photos yet</div>';
                return;
            }

            photos.forEach(function (p, i) {
                const img = document.createElement('img');
                img.src   = p.dataUrl;
                img.title = p.ts;
                img.addEventListener('click', function () { openViewer(i); });
                galleryGrid.appendChild(img);
            });
        }

        /* ══════════════════════════════════════════════
           VIEWER
        ══════════════════════════════════════════════ */
        function openViewer(idx) {
            viewingIndex              = idx;
            viewerImg.src             = photos[idx].dataUrl;
            camGallery.style.display  = 'none';
            photoViewer.style.display = 'flex';
            openBtn.style.display     = 'none';
        }

        /* ══════════════════════════════════════════════
           CAPTURE
        ══════════════════════════════════════════════ */
        function capturePhoto() {
            if (!stream) return;
            if (isUploading) return;   // ← block double-click
            isUploading = true;
            captureBtn.disabled = true;

            flashEl.classList.add('go');
            setTimeout(function () { flashEl.classList.remove('go'); }, 160);

            // Draw frame to hidden canvas
            canvas.width  = video.videoWidth  || 640;
            canvas.height = video.videoHeight || 480;
            const ctx = canvas.getContext('2d');

            if (facingMode === 'user') {
                ctx.translate(canvas.width, 0);
                ctx.scale(-1, 1);
            }
            ctx.drawImage(video, 0, 0);

            const dataUrl  = canvas.toDataURL('image/jpeg', 0.92);
            const filename = 'photo_' + Date.now() + '.jpg';

            photos.push({ dataUrl: dataUrl, ts: new Date().toLocaleTimeString() });
            downloadLink.href     = dataUrl;
            downloadLink.download = filename;

            function releaseGuard() {
                isUploading         = false;
                captureBtn.disabled = false;
            }

            // Get GPS location then upload
            function doUpload(latitude, longitude, location_name) {
                fetch('/salesperson_tracking/save_photo', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        jsonrpc: '2.0',
                        method:  'call',
                        id:      Date.now(),
                        params:  {
                            image_data:    dataUrl,
                            filename:      filename,
                            latitude:      latitude,
                            longitude:     longitude,
                            location_name: location_name,
                        },
                    }),
                })
                .then(function (res) { return res.json(); })
                .then(function (data) {
                    var result = data.result;
                    camLabel.textContent = result && result.success
                        ? '✓ Photo saved in Odoo!'
                        : (result && result.message ? result.message : 'Upload failed');
                    if (result && result.success) snapRow.style.display = 'flex';
                })
                .catch(function (err) {
                    console.error('Photo upload error:', err);
                    camLabel.textContent = 'Upload failed — network error';
                })
                .finally(releaseGuard);   // ← always unlock after response
            }

            // Try to get GPS — upload either way
            if (navigator.geolocation) {
                camLabel.textContent = 'Getting location…';
                navigator.geolocation.getCurrentPosition(
                    function (pos) {
                        var lat = pos.coords.latitude.toFixed(7);
                        var lng = pos.coords.longitude.toFixed(7);

                        // Reverse geocode via Nominatim to get human-readable address
                        fetch(
                            'https://nominatim.openstreetmap.org/reverse'
                            + '?format=jsonv2&lat=' + lat + '&lon=' + lng,
                            { headers: { 'Accept-Language': 'en' } }
                        )
                        .then(function (r) { return r.json(); })
                        .then(function (geo) {
                            var addr = geo && geo.display_name
                                ? geo.display_name
                                : lat + ', ' + lng;
                            doUpload(lat, lng, addr);
                        })
                        .catch(function () {
                            // Nominatim failed — use raw coords
                            doUpload(lat, lng, lat + ', ' + lng);
                        });
                    },
                    function (err) {
                        var reasons = {
                            1: 'Permission denied — allow location in browser settings',
                            2: 'Position unavailable — GPS/network error',
                            3: 'Timeout — GPS took too long',
                        };
                        console.warn('📍 Geolocation error code', err.code, ':', reasons[err.code] || err.message);
                        camLabel.textContent = '' + (reasons[err.code] || 'Location error');
                        doUpload(null, null, null);
                    },
                    { timeout: 10000, maximumAge: 60000, enableHighAccuracy: false }
                );
            } else {
                doUpload(null, null, null);
            }

            // GPS fetch done above in doUpload()

            snapRow.style.display = 'flex';
            setTimeout(function () {
                if (stream) snapRow.style.display = 'none';
            }, 2000);

            renderGallery();
            camGallery.style.display = 'none';
        }

        openBtn.addEventListener('click',    function () { startCamera(facingMode); });
        stopBtn.addEventListener('click',    closeCamera);
        captureBtn.addEventListener('click', capturePhoto);

        flipBtn.addEventListener('click', function () {
            facingMode = facingMode === 'user' ? 'environment' : 'user';
            startCamera(facingMode);
        });

        downloadLink.addEventListener('click', function (e) {
            e.preventDefault();
            var a      = document.createElement('a');
            a.href     = downloadLink.href;
            a.download = downloadLink.download;
            a.click();
        });

        pvBack.addEventListener('click', function () {
            photoViewer.style.display = 'none';
            renderGallery();
            if (!stream) openBtn.style.display = 'inline-flex';
        });

        pvDownload.addEventListener('click', function () {
            var a    = document.createElement('a');
            a.href     = photos[viewingIndex].dataUrl;
            a.download = 'photo_' + Date.now() + '.jpg';
            a.click();
        });

        pvDelete.addEventListener('click', function () {
            photos.splice(viewingIndex, 1);
            photoViewer.style.display = 'none';
            if (photos.length > 0) renderGallery();
            else camGallery.style.display = 'none';
            if (!stream) openBtn.style.display = 'inline-flex';
        });

        clearAllBtn.addEventListener('click', function () {
            photos = [];
            renderGallery();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        // DOM already ready (script loaded after HTML parsed)
        init();
    }

})();