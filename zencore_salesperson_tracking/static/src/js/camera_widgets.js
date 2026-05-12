(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);

    let stream = null;
    let facingMode = 'environment';

    const video      = $('selfieVideo');
    const canvas     = $('selfieCanvas');
    const previewBox = $('previewBox');
    const captureBtn = $('captureBtn');
    const stopCamBtn = $('stopCamBtn');   // renamed from stopBtn to avoid conflict
    const openCamBtn = $('openCameraBtn');

    async function startCamera() {
        if (stream) {
            stream.getTracks().forEach(t => t.stop());
            stream = null;
        }
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: facingMode }, width: { ideal: 1280 }, height: { ideal: 960 } },
                audio: false,
            });
            if (video) {
                video.srcObject = stream;
                video.play();
            }
            if (previewBox) previewBox.style.display = 'flex';
        } catch(e) {
            console.error('Camera error:', e);
            const notice = $('noticeBox');
            if (notice) {
                notice.innerHTML = `<strong>Camera Error</strong> ${e.message}`;
                notice.style.background = '#fee2e2';
            }
        }
    }

    function capturePhoto() {
        if (!stream || !canvas || !video) return;
        canvas.width  = video.videoWidth  || 640;
        canvas.height = video.videoHeight || 480;
        const ctx = canvas.getContext('2d');
        if (facingMode === 'user') {
            ctx.translate(canvas.width, 0);
            ctx.scale(-1, 1);
        }
        ctx.drawImage(video, 0, 0);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.92);

        const sendPhoto = (extraParams) => {
            fetch('/salesperson_tracking/save_photo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonrpc: '2.0', method: 'call', id: Date.now(),
                    params: Object.assign({
                        image_data: dataUrl,
                        filename: `photo_${Date.now()}.jpg`,
                    }, extraParams),
                }),
            }).then(() => {
                const snapRow = $('snapRow');
                if (snapRow) {
                    snapRow.style.display = 'flex';
                    setTimeout(() => { snapRow.style.display = 'none'; }, 2000);
                }
            }).catch(console.error);
        };

        navigator.geolocation.getCurrentPosition(
            (pos) => sendPhoto({ latitude: pos.coords.latitude, longitude: pos.coords.longitude, location_name: 'Current location' }),
            ()    => sendPhoto({}),
        );
    }

    function closeCamera() {
        if (stream) {
            stream.getTracks().forEach(t => t.stop());
            stream = null;
        }
        if (previewBox) previewBox.style.display = 'none';
    }

    if (openCamBtn) openCamBtn.addEventListener('click', startCamera);
    if (captureBtn) captureBtn.addEventListener('click', capturePhoto);
    if (stopCamBtn) stopCamBtn.addEventListener('click', closeCamera);
})();
