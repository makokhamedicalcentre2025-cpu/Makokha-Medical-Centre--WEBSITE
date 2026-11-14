(function initializePublicEnhancements() {
    const reader = document.getElementById('publicContentReader');
    const readerTitle = document.getElementById('publicContentReaderTitle');
    const readerBody = document.getElementById('publicContentReaderBody');
    let lastReaderTrigger = null;

    function refreshReadableSummaries(root = document) {
        root.querySelectorAll('[data-readable-copy]').forEach((copy) => {
            const trigger = copy.parentElement.querySelector('[data-readable-trigger]');
            if (!trigger) return;
            if (!copy.getClientRects().length) {
                copy.classList.remove('is-clamped');
                trigger.hidden = true;
                return;
            }

            copy.classList.add('is-clamped');
            const isOverflowing = copy.scrollHeight > copy.clientHeight + 1;
            copy.classList.toggle('is-clamped', isOverflowing);
            trigger.hidden = !isOverflowing;
        });
    }

    window.refreshReadableSummaries = refreshReadableSummaries;
    refreshReadableSummaries();
    window.addEventListener('resize', () => refreshReadableSummaries());

    document.addEventListener('click', (event) => {
        const mediaToggle = event.target.closest('[data-event-media-toggle]');
        if (mediaToggle) {
            const switcher = mediaToggle.closest('[data-event-media-switcher]');
            if (!switcher) return;

            const selectedMedia = mediaToggle.dataset.eventMediaToggle;
            switcher.querySelectorAll('[data-event-media-toggle]').forEach((button) => {
                const isSelected = button === mediaToggle;
                button.classList.toggle('is-active', isSelected);
                button.setAttribute('aria-pressed', String(isSelected));
            });
            switcher.querySelectorAll('[data-event-media-panel]').forEach((panel) => {
                panel.hidden = panel.dataset.eventMediaPanel !== selectedMedia;
            });
            if (selectedMedia !== 'video') {
                const video = switcher.querySelector('video');
                if (video) video.pause();
            }
            return;
        }

        const readMore = event.target.closest('[data-readable-trigger]');
        if (readMore && reader && readerTitle && readerBody) {
            const copy = readMore.parentElement.querySelector('[data-readable-copy]');
            if (!copy) return;
            lastReaderTrigger = readMore;
            readerTitle.textContent = readMore.dataset.readerTitle || 'More information';
            readerBody.textContent = copy.textContent.trim();
            document.querySelectorAll('.event-media video').forEach((video) => video.pause());
            reader.showModal();
            return;
        }

        if (event.target.closest('[data-reader-close]') && reader && reader.open) {
            reader.close();
        }
    });

    if (reader) {
        reader.addEventListener('click', (event) => {
            if (event.target === reader) reader.close();
        });

        reader.addEventListener('close', () => {
            if (lastReaderTrigger && lastReaderTrigger.isConnected) {
                lastReaderTrigger.focus({ preventScroll: true });
            }
            lastReaderTrigger = null;
        });
    }
})();
