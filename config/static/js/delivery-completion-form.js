(() => {
  const root = document.getElementById('pickupCompletionShell');
  if (!root) return;

  const cameraStrings = {
    notSupported: 'Your browser does not support camera access.',
    chooseFilesOnly: 'Choose files to attach photos.',
    ready: 'Camera ready. Take a photo to attach it to the form.',
    added: 'Photo added to the form.',
    stopped: 'Camera stopped.',
    selectOrCamera: 'Choose files or use the camera to attach photos.',
    chequeSelectOrCamera: 'Use the camera or choose an existing photo of the cheque.',
    denied: 'Camera permission was denied or is not available.',
    filesSelected: 'Files selected.',
    previewTitle: 'Selected photos',
  };
  if (window.LTGCameraUpload) {
    window.LTGCameraUpload.init(root, cameraStrings);
  }

  const paymentStatus = root.querySelector('#paymentStatus');
  const paymentAmountInput = root.querySelector('#paymentAmount');
  const paymentAmountSummary = root.querySelector('#paymentAmountSummary');
  const paymentEntriesFeedback = root.querySelector('#paymentEntriesFeedback');
  const paymentEntryMethods = Array.from(root.querySelectorAll('.js-payment-entry-method'));
  const paymentEntryAmounts = Array.from(root.querySelectorAll('.js-payment-entry-amount'));
  const driverNoteType = root.querySelector('#driverNoteType');
  const pickupSaveAdjustmentNoteButton = root.querySelector('#pickupSaveAdjustmentNoteButton');
  const driverAdjustmentType = root.querySelector('#driverAdjustmentType');
  const driverAdjustmentTypeOptions = driverAdjustmentType && driverAdjustmentType.options ? Array.from(driverAdjustmentType.options) : [];
  const paidFields = root.querySelectorAll('.paid-only');
  const unpaidFields = root.querySelectorAll('.unpaid-only');
  const creditTypeFields = root.querySelectorAll('.driver-credit-type');
  const driverCreditType = root.querySelector('#driverNoteCreditType');
  const driverCreditDumpOption = driverCreditType ? driverCreditType.querySelector('option[value="CREDIT_DUMP"]') : null;
  const driverCreditReturnOption = driverCreditType ? driverCreditType.querySelector('option[value="CREDIT_RETURN"]') : null;
  const driverFinancialAmountWrapper = root.querySelector('#driverFinancialAmountWrapper');
  const driverProductLinesWrapper = root.querySelector('#driverProductLinesWrapper');
  const driverReasonWrapper = root.querySelector('#driverReasonWrapper');
  const driverReasonSelect = root.querySelector('#driverReasonSelect');
  const driverDescriptionLabel = root.querySelector('#driverDescriptionLabel');
  const driverDescriptionHelp = root.querySelector('#driverDescriptionHelp');
  const driverFinancialAmountInput = driverFinancialAmountWrapper ? driverFinancialAmountWrapper.querySelector('input[name="driver_note_monto"]') : null;
  const canvas = root.querySelector('#signatureCanvas');
  const signatureInput = root.querySelector('#signatureData');
  const clearSignature = root.querySelector('#clearSignature');

  const paymentStrings = {
    customerPays: 'Customer must pay',
    customerReceives: 'Customer must receive',
    noPaymentRequired: 'No payment should be collected from the customer.',
    entriesMatch: 'Entered payments match the required total.',
    remaining: 'Remaining amount to collect:',
    exceeded: 'Entered payments exceed the required total by',
    removeIncomingPayments: 'Do not register incoming payments. This delivery results in money due back to the customer.',
  };

  function formatMoney(value) {
    return `$${Number(value || 0).toFixed(2)}`;
  }

  function getDriverDraftCreditAmount() {
    if (!driverNoteType || driverNoteType.value !== 'CREDITO') return 0;
    const isProduct = !driverAdjustmentType || driverAdjustmentType.value === 'PRODUCTO';
    if (!isProduct) return Number((driverFinancialAmountInput && driverFinancialAmountInput.value) || '0');
    return Array.from(root.querySelectorAll('#driverProductLinesWrapper .js-driver-adjustment-amount')).reduce((total, input) => total + Number(input.value || '0'), 0);
  }

  function getDriverDraftDebitAmount() {
    if (!driverNoteType || driverNoteType.value !== 'DEBITO') return 0;
    const isProduct = !driverAdjustmentType || driverAdjustmentType.value === 'PRODUCTO';
    if (!isProduct) return Number((driverFinancialAmountInput && driverFinancialAmountInput.value) || '0');
    return Array.from(root.querySelectorAll('#driverProductLinesWrapper .js-driver-adjustment-amount')).reduce((total, input) => total + Number(input.value || '0'), 0);
  }

  function getSignedPaymentDelta() {
    if (!paymentAmountInput) return 0;
    const baseBalance = Number(paymentAmountInput.dataset.baseBalance || paymentAmountInput.defaultValue || '0');
    return baseBalance - getDriverDraftCreditAmount() + getDriverDraftDebitAmount();
  }

  function updatePaymentEntriesValidation(requiredAmount, signedDelta) {
    if (!paymentEntriesFeedback) return;
    const totalEntered = paymentEntryAmounts.reduce((sum, input) => sum + Number(input.value || '0'), 0);
    paymentEntriesFeedback.classList.remove('text-danger', 'text-success', 'text-muted');
    paymentEntryAmounts.forEach((input) => input.classList.remove('is-invalid', 'is-valid'));
    if (!paymentStatus || paymentStatus.value !== 'PAGADO') {
      paymentEntriesFeedback.textContent = '';
      return;
    }
    if (signedDelta < 0) {
      paymentEntriesFeedback.textContent = paymentStrings.removeIncomingPayments;
      paymentEntriesFeedback.classList.add(totalEntered > 0 ? 'text-danger' : 'text-muted');
      return;
    }
    if (requiredAmount === 0) {
      paymentEntriesFeedback.textContent = paymentStrings.noPaymentRequired;
      paymentEntriesFeedback.classList.add(totalEntered > 0 ? 'text-danger' : 'text-muted');
      return;
    }
    if (Math.abs(totalEntered - requiredAmount) < 0.005) {
      paymentEntriesFeedback.textContent = paymentStrings.entriesMatch;
      paymentEntriesFeedback.classList.add('text-success');
      return;
    }
    paymentEntriesFeedback.textContent = totalEntered > requiredAmount
      ? `${paymentStrings.exceeded} ${formatMoney(totalEntered - requiredAmount)}.`
      : `${paymentStrings.remaining} ${formatMoney(requiredAmount - totalEntered)}.`;
    paymentEntriesFeedback.classList.add('text-danger');
  }

  function updatePaymentAmountSuggestion() {
    if (!paymentAmountInput) return;
    const signedDelta = getSignedPaymentDelta();
    const suggestedAmount = Math.max(signedDelta, 0);
    paymentAmountInput.max = suggestedAmount.toFixed(2);
    paymentAmountInput.readOnly = true;
    paymentAmountInput.value = suggestedAmount.toFixed(2);
    paymentAmountInput.dataset.requiredAmount = suggestedAmount.toFixed(2);
    paymentAmountInput.dataset.signedDelta = signedDelta.toFixed(2);
    if (paymentAmountSummary) {
      if (signedDelta < 0) {
        paymentAmountSummary.textContent = `${paymentStrings.customerReceives} ${formatMoney(Math.abs(signedDelta))}.`;
        paymentAmountSummary.className = 'form-text fw-semibold text-danger';
      } else if (suggestedAmount === 0) {
        paymentAmountSummary.textContent = paymentStrings.noPaymentRequired;
        paymentAmountSummary.className = 'form-text fw-semibold text-muted';
      } else {
        paymentAmountSummary.textContent = `${paymentStrings.customerPays} ${formatMoney(suggestedAmount)}.`;
        paymentAmountSummary.className = 'form-text fw-semibold text-success';
      }
    }
    updatePaymentEntriesValidation(suggestedAmount, signedDelta);
  }

  function togglePaymentEntryFields() {
    if (!paymentStatus) return;
    paymentEntryMethods.forEach((select) => {
      const index = select.dataset.entryIndex;
      const method = select.value;
      root.querySelectorAll(`.payment-entry-field[data-entry-index="${index}"]`).forEach((element) => {
        const methods = (element.dataset.methods || '').split(',').map((value) => value.trim()).filter(Boolean);
        const shouldShow = paymentStatus.value === 'PAGADO' && method && methods.includes(method);
        element.classList.toggle('d-none', !shouldShow);
        element.querySelectorAll('input, select, textarea, button').forEach((field) => {
          field.disabled = !shouldShow;
          if (!shouldShow) {
            if (field.type === 'file') {
              field.value = '';
              field.dispatchEvent(new Event('change', { bubbles: true }));
            } else if (field.type !== 'button' && field.type !== 'submit') {
              field.value = '';
            }
          }
        });
      });
    });
  }

  function setAdjustmentInputState(wrapper, enabled) {
    if (!wrapper) return;
    wrapper.classList.toggle('d-none', !enabled);
    wrapper.querySelectorAll('input, select, textarea').forEach((field) => {
      field.disabled = !enabled;
    });
  }

  function toggleDriverAdjustmentMode() {
    const isProduct = !driverAdjustmentType || driverAdjustmentType.value === 'PRODUCTO';
    const isDebit = driverNoteType && driverNoteType.value === 'DEBITO';
    setAdjustmentInputState(driverProductLinesWrapper, isProduct);
    setAdjustmentInputState(driverFinancialAmountWrapper, !isProduct);
    if (driverCreditType) {
      driverCreditType.disabled = !(driverNoteType && driverNoteType.value === 'CREDITO');
      if (driverCreditDumpOption) driverCreditDumpOption.disabled = false;
      if (driverCreditReturnOption) driverCreditReturnOption.disabled = !isProduct;
      if (driverCreditType.value !== 'CREDIT_DUMP' && !isProduct) driverCreditType.value = 'CREDIT_DUMP';
    }
    driverAdjustmentTypeOptions.forEach((option) => {
      const nextLabel = isDebit ? option.dataset.debitLabel : option.dataset.creditLabel;
      if (nextLabel) option.textContent = nextLabel;
    });
    if (driverReasonWrapper && driverReasonSelect) {
      driverReasonWrapper.classList.toggle('d-none', isDebit);
      if (isDebit) driverReasonSelect.value = 'OTHER';
    }
    if (driverDescriptionLabel) driverDescriptionLabel.textContent = isDebit ? driverDescriptionLabel.dataset.debitLabel : driverDescriptionLabel.dataset.creditLabel;
    if (driverDescriptionHelp) driverDescriptionHelp.textContent = isDebit ? driverDescriptionHelp.dataset.debitHelp : driverDescriptionHelp.dataset.creditHelp;
    updatePaymentAmountSuggestion();
  }

  function recalculateDriverAdjustmentAmounts() {
    root.querySelectorAll('.js-driver-adjustment-amount').forEach((amountInput) => {
      const lineId = amountInput.id;
      const packageInput = root.querySelector(`.js-driver-adjustment-package-qty[data-amount-target="${lineId}"]`);
      if (!packageInput || packageInput.disabled) return;
      const unitInput = root.querySelector(`.js-driver-adjustment-unit-qty[data-amount-target="${lineId}"]`);
      const packagePrice = Number(packageInput.dataset.packagePrice || '0');
      const unitsPerPackage = Math.max(Number(packageInput.dataset.unitsPerPackage || '1'), 1);
      const packageQty = Number(packageInput.value || '0');
      const unitQty = unitInput && !unitInput.disabled ? Number(unitInput.value || '0') : 0;
      const total = (packageQty * packagePrice) + ((packagePrice / unitsPerPackage) * unitQty);
      amountInput.value = total > 0 ? total.toFixed(2) : '';
    });
    updatePaymentAmountSuggestion();
  }

  function togglePaymentSections() {
    if (!paymentStatus) return;
    const status = paymentStatus.value;
    paidFields.forEach((element) => element.classList.toggle('d-none', status !== 'PAGADO'));
    unpaidFields.forEach((element) => element.classList.toggle('d-none', status !== 'NO_PAGADO'));
    updatePaymentAmountSuggestion();
    togglePaymentEntryFields();
  }

  function toggleDriverNoteFields() {
    const isCredit = driverNoteType && driverNoteType.value === 'CREDITO';
    creditTypeFields.forEach((element) => element.classList.toggle('d-none', !isCredit));
    if (driverCreditType && isCredit && !driverCreditType.value) driverCreditType.value = 'CREDIT_DUMP';
    toggleDriverAdjustmentMode();
    syncPickupSaveNoteButton();
  }

  function syncPickupSaveNoteButton() {
    if (!pickupSaveAdjustmentNoteButton || !driverNoteType) return;
    pickupSaveAdjustmentNoteButton.disabled = !driverNoteType.value;
  }

  if (paymentStatus) paymentStatus.addEventListener('change', togglePaymentSections);
  paymentEntryMethods.forEach((field) => field.addEventListener('change', togglePaymentEntryFields));
  paymentEntryAmounts.forEach((field) => field.addEventListener('input', () => {
    const requiredAmount = Number(paymentAmountInput?.dataset.requiredAmount || paymentAmountInput?.value || '0');
    const signedDelta = Number(paymentAmountInput?.dataset.signedDelta || requiredAmount || '0');
    updatePaymentEntriesValidation(requiredAmount, signedDelta);
  }));
  if (driverNoteType) {
    driverNoteType.addEventListener('change', toggleDriverNoteFields);
    driverNoteType.addEventListener('change', toggleDriverAdjustmentMode);
  }
  if (driverAdjustmentType) driverAdjustmentType.addEventListener('change', toggleDriverAdjustmentMode);
  if (driverFinancialAmountInput) driverFinancialAmountInput.addEventListener('input', updatePaymentAmountSuggestion);
  root.querySelectorAll('.js-driver-adjustment-package-qty, .js-driver-adjustment-unit-qty').forEach((field) => field.addEventListener('input', recalculateDriverAdjustmentAmounts));

  if (window.LTGFormDraftRestore) {
    window.LTGFormDraftRestore.restoreFormDraft(root, 'pickup-form-draft-data');
  }

  togglePaymentSections();
  toggleDriverNoteFields();
  toggleDriverAdjustmentMode();
  syncPickupSaveNoteButton();
  recalculateDriverAdjustmentAmounts();

  if (!canvas || !signatureInput || !clearSignature) return;
  const context = canvas.getContext('2d');
  let drawing = false;

  function resizeCanvas() {
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.offsetWidth || canvas.parentElement.offsetWidth;
    canvas.width = width * ratio;
    canvas.height = 220 * ratio;
    context.scale(ratio, ratio);
    context.lineWidth = 2;
    context.lineCap = 'round';
  }
  function pointFromEvent(event) {
    const rect = canvas.getBoundingClientRect();
    const source = event.touches ? event.touches[0] : event;
    return { x: source.clientX - rect.left, y: source.clientY - rect.top };
  }
  function startDrawing(event) {
    drawing = true;
    const point = pointFromEvent(event);
    context.beginPath();
    context.moveTo(point.x, point.y);
    event.preventDefault();
  }
  function draw(event) {
    if (!drawing) return;
    const point = pointFromEvent(event);
    context.lineTo(point.x, point.y);
    context.stroke();
    signatureInput.value = canvas.toDataURL('image/png');
    event.preventDefault();
  }
  function stopDrawing() { drawing = false; }

  clearSignature.addEventListener('click', () => {
    context.clearRect(0, 0, canvas.width, canvas.height);
    signatureInput.value = '';
  });
  canvas.addEventListener('mousedown', startDrawing);
  canvas.addEventListener('mousemove', draw);
  canvas.addEventListener('mouseup', stopDrawing);
  canvas.addEventListener('mouseleave', stopDrawing);
  canvas.addEventListener('touchstart', startDrawing, { passive: false });
  canvas.addEventListener('touchmove', draw, { passive: false });
  canvas.addEventListener('touchend', stopDrawing);
  window.addEventListener('resize', resizeCanvas);
  resizeCanvas();
  if (window.LTGFormDraftRestore) {
    window.LTGFormDraftRestore.restoreSignatureFromHiddenInput(canvas, signatureInput);
  }
})();
