(function initEventVideoPreview() {
    const maxVideoFileBytes = 40 * 1024 * 1024;
    const maxVideoFileMegabytes = Math.round(maxVideoFileBytes / 1024 / 1024);
    const input = document.getElementById('eventVideoUpload');
    const preview = document.querySelector('[data-event-video-preview]');
    const video = preview && preview.querySelector('video');
    const status = preview && preview.querySelector('[data-event-video-status]');
    if (!input || !preview || !video || !status) return;

    let previewUrl = '';
    input.addEventListener('change', () => {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = '';
        video.pause();
        video.removeAttribute('src');
        video.load();
        const file = input.files && input.files[0];
        if (!file) {
            preview.hidden = true;
            input.setCustomValidity('');
            status.textContent = '';
            return;
        }

        const exceedsSizeLimit = file.size > maxVideoFileBytes;
        previewUrl = URL.createObjectURL(file);
        const selectedPreviewUrl = previewUrl;
        input.setCustomValidity(
            exceedsSizeLimit
                ? `Each video must be ${maxVideoFileMegabytes} MB or smaller.`
                : ''
        );
        video.muted = true;
        video.controls = true;
        video.playsInline = true;
        preview.hidden = false;
        status.textContent = exceedsSizeLimit
            ? `This video exceeds the ${maxVideoFileMegabytes} MB limit.`
            : 'Loading video preview…';

        video.addEventListener('loadedmetadata', () => {
            if (previewUrl !== selectedPreviewUrl) return;
            const duration = Number.isFinite(video.duration) ? video.duration : 0;
            const lengthMessage = duration
                ? `Duration: ${Math.round(duration)} seconds.`
                : 'Duration could not be determined.';
            const sizeMessage = exceedsSizeLimit
                ? ` This video exceeds the ${maxVideoFileMegabytes} MB limit.`
                : duration > 40
                    ? ' This video exceeds the 40-second limit.'
                    : '';
            const playRequest = video.play();
            if (playRequest && typeof playRequest.then === 'function') {
                playRequest.then(() => {
                    if (previewUrl === selectedPreviewUrl) {
                        status.textContent = `${lengthMessage}${sizeMessage} Preview is playing muted.`;
                    }
                }).catch((error) => {
                    if (previewUrl !== selectedPreviewUrl || video.error) return;
                    if (error && error.name !== 'NotAllowedError' && error.name !== 'AbortError') {
                        input.setCustomValidity('This browser cannot play this video. Choose a browser-compatible MP4 or WebM video.');
                        status.textContent = 'Preview failed. Choose an MP4 or WebM video with a browser-compatible codec.';
                    } else {
                        status.textContent = `${lengthMessage}${sizeMessage} Press Play to preview.`;
                    }
                });
            } else {
                status.textContent = `${lengthMessage}${sizeMessage} Press Play to preview.`;
            }
        }, { once: true });
        video.addEventListener('error', () => {
            if (previewUrl !== selectedPreviewUrl) return;
            input.setCustomValidity('This browser cannot play this video. Choose a browser-compatible MP4 or WebM video.');
            status.textContent = 'Preview failed. Choose an MP4 or WebM video with a browser-compatible codec.';
        }, { once: true });
        video.src = selectedPreviewUrl;
        video.load();
    });
})();
