(function initEventVideoPreview() {
    const input = document.getElementById('eventVideoUpload');
    const preview = document.querySelector('[data-event-video-preview]');
    const video = preview && preview.querySelector('video');
    const status = preview && preview.querySelector('[data-event-video-status]');
    if (!input || !preview || !video || !status) return;

    let previewUrl = '';
    input.addEventListener('change', () => {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        const file = input.files && input.files[0];
        if (!file) {
            video.removeAttribute('src');
            preview.hidden = true;
            return;
        }

        previewUrl = URL.createObjectURL(file);
        video.src = previewUrl;
        status.textContent = file.size > 5 * 1024 * 1024
            ? 'This video exceeds the 5 MB limit.'
            : 'Loading video preview…';
        preview.hidden = false;
    });

    video.addEventListener('loadedmetadata', () => {
        const file = input.files && input.files[0];
        if (!file) return;
        const duration = Number.isFinite(video.duration) ? video.duration : 0;
        const lengthMessage = duration
            ? `Duration: ${Math.round(duration)} seconds.`
            : 'Duration could not be determined.';
        const sizeMessage = file.size > 5 * 1024 * 1024
            ? ' This video exceeds the 5 MB limit.'
            : duration > 40
                ? ' This video exceeds the 40-second limit.'
                : ' Ready to upload.';
        status.textContent = `${lengthMessage}${sizeMessage}`;
    });
})();
