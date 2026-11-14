(function initializeImageUploadPreviews() {
    const previewInputs = document.querySelectorAll('input[type="file"][data-image-upload-preview]');
    const activeUrls = [];

    function isImageFile(file) {
        return file.type.startsWith('image/') || /\.(jpe?g|png|webp|gif)$/i.test(file.name);
    }

    function createPreviewView(caption, modifier, file, previewUrl) {
        const figure = document.createElement('figure');
        figure.className = 'admin-image-upload-preview-view';

        const label = document.createElement('figcaption');
        label.textContent = caption;
        figure.appendChild(label);

        const frame = document.createElement('div');
        frame.className = `admin-image-upload-preview-frame admin-image-upload-preview-frame--${modifier}`;

        const image = document.createElement('img');
        image.src = previewUrl;
        image.alt = `${caption}: ${file.name}`;
        image.addEventListener('error', () => {
            image.alt = `Preview unavailable for ${file.name}`;
        }, { once: true });

        frame.appendChild(image);
        figure.appendChild(frame);
        return figure;
    }

    previewInputs.forEach((input) => {
        const ratio = input.dataset.imagePreviewRatio || '4 / 3';
        const positionSource = input.dataset.imagePreviewPositionSource
            ? document.getElementById(input.dataset.imagePreviewPositionSource)
            : null;
        const preview = document.createElement('div');
        preview.className = 'admin-image-upload-preview';
        preview.hidden = true;
        preview.setAttribute('aria-live', 'polite');
        input.insertAdjacentElement('afterend', preview);

        const syncWebsitePosition = () => {
            if (!positionSource) return;
            let position = { x: 50, y: 50 };
            try {
                const parsed = JSON.parse(positionSource.value || '{}');
                const x = Number(parsed.x);
                const y = Number(parsed.y);
                if (Number.isFinite(x) && Number.isFinite(y)) {
                    position = {
                        x: Math.min(100, Math.max(0, x)),
                        y: Math.min(100, Math.max(0, y))
                    };
                }
            } catch (error) {
                position = { x: 50, y: 50 };
            }
            preview.querySelectorAll('.admin-image-upload-preview-frame--website img').forEach((image) => {
                image.style.objectPosition = `${position.x}% ${position.y}%`;
            });
        };
        if (positionSource) positionSource.addEventListener('input', syncWebsitePosition);

        let previewUrls = [];
        input.addEventListener('change', () => {
            previewUrls.forEach((url) => {
                URL.revokeObjectURL(url);
                const urlIndex = activeUrls.indexOf(url);
                if (urlIndex !== -1) activeUrls.splice(urlIndex, 1);
            });
            previewUrls = [];
            preview.replaceChildren();

            const imageFiles = Array.from(input.files || []).filter(isImageFile);
            if (!imageFiles.length) {
                preview.hidden = true;
                return;
            }

            const grid = document.createElement('div');
            grid.className = 'admin-image-upload-preview-grid';
            const perFileRatio = input.dataset.imagePreviewRatio || ratio;

            imageFiles.forEach((file) => {
                const previewUrl = URL.createObjectURL(file);
                previewUrls.push(previewUrl);
                activeUrls.push(previewUrl);

                const card = document.createElement('article');
                card.className = 'admin-image-upload-preview-card';

                const name = document.createElement('p');
                name.className = 'admin-image-upload-preview-name';
                name.textContent = `${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)`;
                card.appendChild(name);

                const views = document.createElement('div');
                views.className = 'admin-image-upload-preview-views';
                views.style.setProperty('--image-preview-ratio', perFileRatio);
                views.appendChild(createPreviewView('Full image', 'full', file, previewUrl));
                views.appendChild(createPreviewView('Website view', 'website', file, previewUrl));
                card.appendChild(views);
                grid.appendChild(card);
            });

            preview.appendChild(grid);
            preview.hidden = false;
            syncWebsitePosition();
        });
    });

    window.addEventListener('pagehide', () => {
        activeUrls.forEach((url) => URL.revokeObjectURL(url));
    }, { once: true });
})();
