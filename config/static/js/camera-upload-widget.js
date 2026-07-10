(function () {
  function initCameraUploadWidgets(root, strings) {
    const scope = root && root.querySelectorAll ? root : document;
    const cameraWidgets = scope.querySelectorAll('[data-camera-upload]');
    const supportsInlineCamera = Boolean(
      window.isSecureContext && navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function'
    );

    function defaultStatusMessage(widget) {
      if (widget.hasAttribute('data-camera-single')) {
        return strings.chequeSelectOrCamera || strings.selectOrCamera;
      }
      return supportsInlineCamera ? strings.selectOrCamera : strings.chooseFilesOnly;
    }

    function setCameraStatus(widget, message) {
      const status = widget.querySelector('[data-camera-status]');
      if (status) {
        status.textContent = message;
      }
    }

    function toggleCameraButtons(widget, active) {
      const startButton = widget.querySelector('[data-camera-start]');
      const captureButton = widget.querySelector('[data-camera-capture]');
      const stopButton = widget.querySelector('[data-camera-stop]');
      const galleryButton = widget.querySelector('[data-cheque-gallery-trigger]');
      if (startButton) {
        startButton.classList.toggle('d-none', active || !supportsInlineCamera);
      }
      if (galleryButton) {
        galleryButton.classList.toggle('d-none', active);
      }
      if (captureButton) {
        captureButton.classList.toggle('d-none', !active);
      }
      if (stopButton) {
        stopButton.classList.toggle('d-none', !active);
      }
    }

    function stopCamera(widget, options) {
      const settings = options || {};
      const preview = widget.querySelector('[data-camera-preview]');
      const video = widget.querySelector('[data-camera-video]');
      const hadStream = Boolean(widget._cameraStream);
      if (widget._cameraStream) {
        widget._cameraStream.getTracks().forEach((track) => track.stop());
        widget._cameraStream = null;
      }
      if (video) {
        video.srcObject = null;
      }
      if (preview) {
        preview.classList.add('d-none');
      }
      toggleCameraButtons(widget, false);
      if (!settings.silent && hadStream) {
        setCameraStatus(widget, strings.stopped);
      }
    }

    function clearFilePreview(widget) {
      const preview = widget.querySelector('[data-file-preview]');
      if (!preview) {
        return;
      }
      if (widget._previewUrls) {
        widget._previewUrls.forEach((url) => URL.revokeObjectURL(url));
      }
      widget._previewUrls = [];
      preview.innerHTML = '';
      preview.classList.add('d-none');
    }

    function setInputFiles(input, files) {
      if (!input || typeof DataTransfer === 'undefined') {
        return;
      }
      const dataTransfer = new DataTransfer();
      Array.from(files || []).forEach((file) => dataTransfer.items.add(file));
      input.files = dataTransfer.files;
    }

    function removeFileAtIndex(widget, removeIndex) {
      const input = widget.querySelector('[data-camera-input]');
      if (!input || typeof DataTransfer === 'undefined') {
        return;
      }
      const remaining = Array.from(input.files || []).filter((_file, index) => index !== removeIndex);
      setInputFiles(input, remaining);
      renderFilePreview(widget, input.files);
      setCameraStatus(
        widget,
        remaining.length ? strings.filesSelected : defaultStatusMessage(widget)
      );
    }

    function renderFilePreview(widget, files) {
      const preview = widget.querySelector('[data-file-preview]');
      if (!preview) {
        return;
      }
      clearFilePreview(widget);
      const isSingle = widget.hasAttribute('data-camera-single');
      const imageFiles = Array.from(files || [])
        .filter((file) => file && String(file.type || '').startsWith('image/'))
        .slice(0, isSingle ? 1 : undefined);
      if (!imageFiles.length) {
        return;
      }
      widget._previewUrls = [];
      const removeLabel = strings.removePhoto || 'Remove';
      imageFiles.forEach((file, index) => {
        const url = URL.createObjectURL(file);
        widget._previewUrls.push(url);
        const col = document.createElement('div');
        col.className = isSingle ? 'col-12' : 'col-6 col-md-4';
        col.innerHTML = `
          <div class="border rounded p-2 h-100 bg-white position-relative">
            <button type="button" class="btn btn-sm btn-danger position-absolute top-0 end-0 m-1 py-0 px-2" data-remove-preview="${index}" aria-label="${removeLabel}" title="${removeLabel}">&times;</button>
            <img src="${url}" alt="${strings.previewTitle} ${index + 1}" class="img-fluid rounded mb-2" style="max-height: 160px; width: 100%; object-fit: contain; background: #0f172a;">
            <div class="small text-muted text-truncate">${file.name}</div>
          </div>
        `;
        preview.appendChild(col);
      });
      preview.classList.remove('d-none');
      preview.querySelectorAll('[data-remove-preview]').forEach((button) => {
        button.addEventListener('click', function (event) {
          event.preventDefault();
          event.stopPropagation();
          const index = Number(button.getAttribute('data-remove-preview'));
          if (Number.isFinite(index)) {
            removeFileAtIndex(widget, index);
          }
        });
      });
    }

    async function startCamera(widget) {
      const preview = widget.querySelector('[data-camera-preview]');
      const video = widget.querySelector('[data-camera-video]');
      if (!supportsInlineCamera || !video) {
        setCameraStatus(widget, strings.notSupported);
        return;
      }

      stopCamera(widget, { silent: true });
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: 'environment' },
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
          audio: false,
        });
        widget._cameraStream = stream;
        video.srcObject = stream;
        // Wait for metadata so capture uses the real frame size.
        if (video.readyState < 1) {
          await new Promise((resolve) => {
            video.onloadedmetadata = resolve;
          });
        }
        try {
          await video.play();
        } catch (_playError) {
          // Autoplay can fail; stream is still usable for capture.
        }
        if (preview) {
          preview.classList.remove('d-none');
        }
        toggleCameraButtons(widget, true);
        setCameraStatus(widget, strings.ready);
      } catch (_error) {
        setCameraStatus(widget, strings.denied);
      }
    }

    function appendCapturedPhoto(widget) {
      const input = widget.querySelector('[data-camera-input]');
      const video = widget.querySelector('[data-camera-video]');
      const cameraCanvas = widget.querySelector('[data-camera-canvas]');
      if (!input || !video || !cameraCanvas || typeof DataTransfer === 'undefined') {
        return;
      }
      const width = video.videoWidth || 0;
      const height = video.videoHeight || 0;
      if (!width || !height) {
        setCameraStatus(widget, strings.denied);
        return;
      }
      // Capture the full camera frame (matches object-fit: contain preview).
      cameraCanvas.width = width;
      cameraCanvas.height = height;
      const cameraContext = cameraCanvas.getContext('2d');
      cameraContext.drawImage(video, 0, 0, width, height);
      cameraCanvas.toBlob(function (blob) {
        if (!blob) {
          return;
        }
        const dataTransfer = new DataTransfer();
        const isSingle = widget.hasAttribute('data-camera-single');
        if (!isSingle) {
          Array.from(input.files || []).forEach((file) => dataTransfer.items.add(file));
        }
        const fileName = `evidence-${Date.now()}.jpg`;
        dataTransfer.items.add(new File([blob], fileName, { type: 'image/jpeg' }));
        input.files = dataTransfer.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
        setCameraStatus(widget, strings.added);
        stopCamera(widget, { silent: true });
      }, 'image/jpeg', 0.92);
    }

    function openGalleryPicker(widget) {
      const input = widget.querySelector('[data-camera-input]');
      if (!input || input.disabled) {
        return;
      }
      const galleryInput = document.createElement('input');
      galleryInput.type = 'file';
      galleryInput.accept = 'image/*';
      galleryInput.addEventListener('change', function () {
        if (!galleryInput.files || !galleryInput.files.length || typeof DataTransfer === 'undefined') {
          return;
        }
        const dataTransfer = new DataTransfer();
        dataTransfer.items.add(galleryInput.files[0]);
        input.files = dataTransfer.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      galleryInput.click();
    }

    cameraWidgets.forEach((widget) => {
      if (widget.dataset.cameraUploadReady === 'true') {
        return;
      }
      widget.dataset.cameraUploadReady = 'true';

      const input = widget.querySelector('[data-camera-input]');
      const startButton = widget.querySelector('[data-camera-start]');
      const captureButton = widget.querySelector('[data-camera-capture]');
      const stopButton = widget.querySelector('[data-camera-stop]');
      const galleryButton = widget.querySelector('[data-cheque-gallery-trigger]');
      const video = widget.querySelector('[data-camera-video]');

      // Show the full frame so capture matches what the driver sees.
      if (video) {
        video.style.objectFit = 'contain';
        video.style.background = '#0f172a';
      }

      toggleCameraButtons(widget, false);
      setCameraStatus(widget, defaultStatusMessage(widget));

      if (input) {
        input.addEventListener('change', function () {
          const isSingle = widget.hasAttribute('data-camera-single');
          if (isSingle && input.files && input.files.length > 1 && typeof DataTransfer !== 'undefined') {
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(input.files[0]);
            input.files = dataTransfer.files;
          }
          const hasFiles = input.files && input.files.length;
          renderFilePreview(widget, input.files);
          setCameraStatus(
            widget,
            hasFiles ? strings.filesSelected : defaultStatusMessage(widget)
          );
        });
      }
      if (startButton) {
        startButton.addEventListener('click', function () {
          startCamera(widget);
        });
      }
      if (captureButton) {
        captureButton.addEventListener('click', function () {
          appendCapturedPhoto(widget);
        });
      }
      if (stopButton) {
        stopButton.addEventListener('click', function () {
          stopCamera(widget);
        });
      }
      if (galleryButton) {
        galleryButton.addEventListener('click', function () {
          openGalleryPicker(widget);
        });
      }
    });

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        cameraWidgets.forEach((widget) => stopCamera(widget, { silent: true }));
      }
    });
  }

  window.LTGCameraUpload = {
    init: initCameraUploadWidgets,
  };
})();
