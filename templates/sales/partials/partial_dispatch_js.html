// ── Partial Dispatch Modal Logic ──
var _dispatchSaleItems = [];
var _dispatchSaleId = null;

function populateDispatchModal(items, allowPartial, hasPendingRefund, pendingRefundInfo) {
    if (allowPartial === undefined) allowPartial = true;

    const tbody = document.getElementById('dispatchItemsBody');
    const toggleContainer = document.getElementById('showDispatchedToggleContainer');
    const toggle = document.getElementById('showDispatchedToggle');
    const partialBtn = document.getElementById('partialDispatchSubmitBtn');
    const fullBtn = document.getElementById('fullDispatchBtn');
    const haltBtn = document.getElementById('haltDispatchBtn');
    const dispatchNowHeader = document.getElementById('dispatchNowColHeader');
    const infoText = document.getElementById('dispatchInfoText');
    const infoBanner = document.getElementById('dispatchInfoBanner');
    if (!tbody) return;

    if (hasPendingRefund) {
        const isHalt = pendingRefundInfo && pendingRefundInfo.is_partial_halt;
        const typeName = isHalt ? 'Halt & Settle' : 'Refund';
        const refNum = pendingRefundInfo && pendingRefundInfo.refund_number ? `(${pendingRefundInfo.refund_number})` : '';
        if (infoBanner && infoText) {
            infoBanner.className = 'alert alert-warning py-2 mb-3 d-flex align-items-center gap-2 border border-warning';
            infoText.innerHTML = `<i class="bi bi-exclamation-triangle-fill text-warning me-1"></i>` +
                `<strong>${typeName} Pending Approval:</strong> Sale has request <strong>${refNum}</strong> awaiting review by the Accountant. ` +
                `Goods dispatch and further halt requests are locked.`;
        }
        if (haltBtn) {
            haltBtn.disabled = true;
            haltBtn.className = 'btn btn-secondary';
            haltBtn.innerHTML = '<i class="bi bi-clock-history me-1"></i>Halt Pending Approval';
            haltBtn.title = 'A refund or halt request is already pending Accountant review.';
        }
        if (fullBtn) {
            fullBtn.disabled = true;
            fullBtn.title = 'Locked while refund request is pending';
        }
        if (partialBtn) {
            partialBtn.disabled = true;
            partialBtn.title = 'Locked while refund request is pending';
        }
    } else {
        if (infoBanner && infoText) {
            infoBanner.className = 'alert alert-info py-2 mb-3 d-flex align-items-center gap-2';
            infoText.innerHTML = allowPartial
                ? 'Review the items below. Click <strong>Full Dispatch</strong> to release all remaining items, or adjust quantities and click <strong>Partial Dispatch</strong> to release only some items now.'
                : 'This shop requires <strong>full dispatch only</strong>. Click <strong>Full Dispatch</strong> to release all remaining items at once.';
        }
        if (haltBtn) {
            haltBtn.disabled = false;
            haltBtn.className = 'btn btn-outline-warning text-dark';
            haltBtn.innerHTML = '<i class="bi bi-stop-circle me-1"></i>Halt & Settle';
            haltBtn.removeAttribute('title');
        }
        if (fullBtn) {
            fullBtn.disabled = false;
            fullBtn.removeAttribute('title');
        }
        if (partialBtn) {
            partialBtn.disabled = false;
            partialBtn.removeAttribute('title');
        }
    }

    if (partialBtn) partialBtn.style.display = allowPartial ? '' : 'none';
    if (dispatchNowHeader) dispatchNowHeader.style.display = allowPartial ? '' : 'none';

    const hasFullyDispatched = items.some(i => i.is_fully_dispatched);
    if (hasFullyDispatched) {
        toggleContainer.style.removeProperty('display');
    } else {
        toggleContainer.style.setProperty('display', 'none', 'important');
        if (toggle) toggle.checked = false;
    }

    function renderRows(showDispatched) {
        tbody.innerHTML = '';
        items.forEach(function(item) {
            const remaining = parseFloat(item.remaining_quantity);
            const ordered = parseFloat(item.quantity);
            const dispatched = parseFloat(item.dispatched_quantity);
            const done = item.is_fully_dispatched;

            if (done && !showDispatched) return;

            const tr = document.createElement('tr');
            if (done) tr.classList.add('table-secondary', 'text-muted');

            let dispatchNowCell;
            if (done) {
                dispatchNowCell = '<span class="text-muted small"><i class="bi bi-check-circle-fill text-success me-1"></i>Dispatched</span>';
            } else if (allowPartial) {
                dispatchNowCell = `<input type="number" class="form-control form-control-sm text-center dispatch-qty-input"
                                data-item-id="${item.id}" data-max="${remaining}"
                                min="0" max="${remaining}" step="1" value="0"
                                ${hasPendingRefund ? 'disabled' : ''}
                                style="width:90px; margin: 0 auto;">`;
            } else {
                dispatchNowCell = `<span class="fw-bold text-success">${remaining}</span>
                    <input type="hidden" class="dispatch-qty-input" data-item-id="${item.id}" data-max="${remaining}" value="${remaining}">`;
            }

            tr.innerHTML = `
                <td class="fw-medium">${item.product_name}<br><small class="text-muted">${item.sku}</small></td>
                <td class="text-center">${ordered}</td>
                <td class="text-center">${dispatched}</td>
                <td class="text-center">${done ? '<span class="badge bg-success">Done</span>' : remaining}</td>
                <td class="text-center" ${!allowPartial ? 'style="display:none"' : ''}>${dispatchNowCell}</td>`;
            tbody.appendChild(tr);
        });
    }

    renderRows(toggle ? toggle.checked : false);
    if (toggle) {
        toggle.onchange = () => renderRows(toggle.checked);
    }
}

function populateDispatchHistory(history) {
    const section = document.getElementById('dispatchHistorySection');
    const body = document.getElementById('dispatchHistoryBody');
    if (!section || !body) return;

    if (!history || history.length === 0) {
        section.style.display = 'none';
        body.innerHTML = '';
        return;
    }

    section.style.display = '';
    body.innerHTML = '';

    history.forEach(function(event, idx) {
        const itemRows = event.items.map(i =>
            `<tr>
                <td class="ps-3 text-muted small">${i.product_name}</td>
                <td class="text-center small fw-medium">${i.qty}</td>
            </tr>`
        ).join('');

        const card = document.createElement('div');
        card.className = 'border rounded-3 mb-2 overflow-hidden';
        card.innerHTML = `
            <div class="d-flex align-items-center justify-content-between px-3 py-2 bg-light">
                <div>
                    <i class="bi bi-send-check text-success me-2"></i>
                    <span class="fw-semibold small">Dispatch ${idx + 1}</span>
                    <span class="text-muted small ms-2">— ${event.dispatched_by}</span>
                </div>
                <span class="badge bg-success-subtle text-success rounded-pill small">
                    <i class="bi bi-clock me-1"></i>${event.dispatched_at}
                </span>
            </div>
            <table class="table table-sm mb-0">
                <thead class="table-light border-top">
                    <tr>
                        <th class="ps-3 small">Product</th>
                        <th class="text-center small">Qty Released</th>
                    </tr>
                </thead>
                <tbody>${itemRows}</tbody>
            </table>`;
        body.appendChild(card);
    });
}

function submitDispatch(fillAll) {
    const inputs = document.querySelectorAll('#dispatchItemsBody .dispatch-qty-input');
    const dispatchItems = [];
    let hasQty = false;

    inputs.forEach(function(inp) {
        const max = parseFloat(inp.dataset.max);
        const qty = fillAll ? max : (parseFloat(inp.value) || 0);
        if (qty > 0) {
            hasQty = true;
            dispatchItems.push({ sale_item_id: parseInt(inp.dataset.itemId), qty: qty });
        }
    });

    if (!hasQty) {
        if (window.showAppToast) {
            window.showAppToast('Please enter at least one quantity greater than 0 to dispatch.', 'warning');
        } else {
            alert('No remaining quantities to dispatch.');
        }
        return;
    }

    const activeId = fillAll ? 'fullDispatchBtn' : 'partialDispatchSubmitBtn';
    const otherId  = fillAll ? 'partialDispatchSubmitBtn' : 'fullDispatchBtn';
    const activeBtn = document.getElementById(activeId);
    const otherBtn  = document.getElementById(otherId);
    const originalHtml = activeBtn ? activeBtn.innerHTML : '';

    if (activeBtn) {
        activeBtn.disabled = true;
        activeBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Dispatching...';
    }
    if (otherBtn) otherBtn.disabled = true;

    const csrfEl = document.querySelector('[name=csrfmiddlewaretoken]');
    const csrfToken = csrfEl ? csrfEl.value : '';

    fetch('/sales/api/sales/' + _dispatchSaleId + '/dispatch/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ items: dispatchItems })
    })
    .then(r => r.ok ? r.json() : r.json().then(e => { throw new Error(e.error || 'Dispatch failed'); }))
    .then(function(res) {
        const modalEl = document.getElementById('partialDispatchModal');
        if (modalEl) {
            const modalInstance = bootstrap.Modal.getInstance(modalEl);
            if (modalInstance) modalInstance.hide();
        }
        if (res.waybill_url) {
            window.open(res.waybill_url, '_blank');
        }
        if (window.showAppToast) {
            window.showAppToast(res.message || 'Dispatch recorded successfully.', 'success');
        }
        setTimeout(() => location.reload(), 800);
    })
    .catch(function(err) {
        if (window.showAppToast) {
            window.showAppToast(err.message, 'danger');
        } else {
            alert(err.message);
        }
        if (activeBtn) {
            activeBtn.disabled = false;
            activeBtn.innerHTML = originalHtml;
        }
        if (otherBtn) otherBtn.disabled = false;
    });
}

function initPartialDispatch() {
    // ── Unified open-modal trigger ──
    document.querySelectorAll('.open-dispatch-modal').forEach(function(btn) {
        if (btn.dataset.dispatchBound) return;
        btn.dataset.dispatchBound = 'true';
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            const saleId = this.dataset.saleId;
            const saleNumber = this.dataset.saleNumber;

            fetch('/sales/api/' + saleId + '/detail/')
                .then(res => {
                    if (!res.ok) throw new Error('Failed to load sale details');
                    return res.json();
                })
                .then(data => {
                    _dispatchSaleId = saleId;
                    const snEl = document.getElementById('dispatchModalSaleNumber');
                    if (snEl) snEl.textContent = '#' + (saleNumber || '');
                    populateDispatchModal(data.items, data.allow_partial_dispatch !== false, data.has_pending_refund, data.pending_refund_info);
                    populateDispatchHistory(data.dispatch_history || []);
                    const modalEl = document.getElementById('partialDispatchModal');
                    if (modalEl) {
                        const modalInstance = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
                        modalInstance.show();
                    }
                })
                .catch(err => {
                    console.error(err);
                    if (window.showAppToast) {
                        window.showAppToast('Failed to load items for dispatch: ' + err.message, 'danger');
                    } else {
                        alert('Failed to load items for dispatch.');
                    }
                });
        });
    });

    // ── Sale Detail modal dispatch button ──
    const saleDetailDispatchBtn = document.getElementById('saleDetailDispatchBtn');
    if (saleDetailDispatchBtn && !saleDetailDispatchBtn.dataset.dispatchBound) {
        saleDetailDispatchBtn.dataset.dispatchBound = 'true';
        saleDetailDispatchBtn.addEventListener('click', function() {
            _dispatchSaleId = this.dataset.saleId;
            const snEl = document.getElementById('dispatchModalSaleNumber');
            if (snEl) snEl.textContent = '#' + (this.dataset.saleNumber || '');
            const allowPartial = window._allowPartialDispatch !== false;
            populateDispatchModal(_dispatchSaleItems, allowPartial, window._saleHasPendingRefund, window._salePendingRefundInfo);
            populateDispatchHistory(window._dispatchHistory || []);
            const saleDetailModal = document.getElementById('saleDetailModal');
            if (saleDetailModal) {
                const sdInstance = bootstrap.Modal.getInstance(saleDetailModal);
                if (sdInstance) sdInstance.hide();
            }
            const modalEl = document.getElementById('partialDispatchModal');
            if (modalEl) {
                const modalInstance = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
                modalInstance.show();
            }
        });
    }

    // ── Full Dispatch: fills all remaining quantities, then submits ──
    const fullDispatchBtn = document.getElementById('fullDispatchBtn');
    if (fullDispatchBtn && !fullDispatchBtn.dataset.dispatchBound) {
        fullDispatchBtn.dataset.dispatchBound = 'true';
        fullDispatchBtn.addEventListener('click', function() { submitDispatch(true); });
    }

    // ── Partial Dispatch: submits only entered quantities ──
    const partialDispatchSubmitBtn = document.getElementById('partialDispatchSubmitBtn');
    if (partialDispatchSubmitBtn && !partialDispatchSubmitBtn.dataset.dispatchBound) {
        partialDispatchSubmitBtn.dataset.dispatchBound = 'true';
        partialDispatchSubmitBtn.addEventListener('click', function() { submitDispatch(false); });
    }

    // ── Halt Dispatch Trigger (opens Halt & Settle Modal) ──
    const haltDispatchBtn = document.getElementById('haltDispatchBtn');
    if (haltDispatchBtn && !haltDispatchBtn.dataset.dispatchBound) {
        haltDispatchBtn.dataset.dispatchBound = 'true';
        haltDispatchBtn.addEventListener('click', function() {
            if (!_dispatchSaleId) return;

            const haltModalEl = document.getElementById('haltDispatchModal');
            if (!haltModalEl) return;

            // Reset modal inputs and errors
            const reasonInput = document.getElementById('haltDispatchReason');
            const feedback = document.getElementById('haltDispatchReasonFeedback');
            const errAlert = document.getElementById('haltDispatchErrorAlert');
            const haltSnEl = document.getElementById('haltModalSaleNumber');
            const dispatchSnEl = document.getElementById('dispatchModalSaleNumber');

            if (reasonInput) {
                reasonInput.value = '';
                reasonInput.classList.remove('is-invalid');
            }
            if (feedback) feedback.style.display = 'none';
            if (errAlert) {
                errAlert.classList.add('d-none');
                errAlert.textContent = '';
            }
            if (haltSnEl && dispatchSnEl) {
                haltSnEl.textContent = dispatchSnEl.textContent;
            }

            // Hide partial dispatch modal and show halt modal
            const pdModalEl = document.getElementById('partialDispatchModal');
            if (pdModalEl) {
                const pdInstance = bootstrap.Modal.getInstance(pdModalEl);
                if (pdInstance) pdInstance.hide();
            }

            const haltInstance = bootstrap.Modal.getInstance(haltModalEl) || new bootstrap.Modal(haltModalEl);
            haltInstance.show();
        });
    }

    // ── Confirm Halt & Settle Submission ──
    const confirmHaltBtn = document.getElementById('confirmHaltDispatchBtn');
    if (confirmHaltBtn && !confirmHaltBtn.dataset.dispatchBound) {
        confirmHaltBtn.dataset.dispatchBound = 'true';
        confirmHaltBtn.addEventListener('click', function() {
            if (!_dispatchSaleId) return;

            const reasonInput = document.getElementById('haltDispatchReason');
            const feedback = document.getElementById('haltDispatchReasonFeedback');
            const errAlert = document.getElementById('haltDispatchErrorAlert');
            const reason = reasonInput ? reasonInput.value.trim() : '';

            if (!reason) {
                if (reasonInput) {
                    reasonInput.classList.add('is-invalid');
                    reasonInput.focus();
                }
                if (feedback) feedback.style.display = 'block';
                return;
            }

            if (reasonInput) reasonInput.classList.remove('is-invalid');
            if (feedback) feedback.style.display = 'none';
            if (errAlert) errAlert.classList.add('d-none');

            confirmHaltBtn.disabled = true;
            const originalHtml = confirmHaltBtn.innerHTML;
            confirmHaltBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Submitting...';

            const csrfEl = document.querySelector('[name=csrfmiddlewaretoken]');
            const csrfToken = csrfEl ? csrfEl.value : '';

            fetch('/sales/api/sales/' + _dispatchSaleId + '/halt-dispatch/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ reason: reason })
            })
            .then(r => r.ok ? r.json() : r.json().then(e => { throw new Error(e.error || 'Failed to halt dispatch'); }))
            .then(function(res) {
                const haltModalEl = document.getElementById('haltDispatchModal');
                if (haltModalEl) {
                    const haltInstance = bootstrap.Modal.getInstance(haltModalEl);
                    if (haltInstance) haltInstance.hide();
                }
                if (window.showAppToast) {
                    window.showAppToast(res.message || 'Dispatch halted and refund request submitted.', 'success');
                }
                setTimeout(() => location.reload(), 900);
            })
            .catch(function(err) {
                confirmHaltBtn.disabled = false;
                confirmHaltBtn.innerHTML = originalHtml;
                if (errAlert) {
                    errAlert.textContent = err.message;
                    errAlert.classList.remove('d-none');
                } else if (window.showAppToast) {
                    window.showAppToast(err.message, 'danger');
                }
            });
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPartialDispatch);
} else {
    initPartialDispatch();
}
