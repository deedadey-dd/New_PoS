/**
 * POS System JavaScript
 */

document.addEventListener('DOMContentLoaded', function () {
    // Sidebar Toggle
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');
    const mainContent = document.querySelector('.main-content');

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', function () {
            sidebar.classList.toggle('collapsed');
            if (mainContent) {
                mainContent.classList.toggle('expanded');
            }
        });
    }

    // Auto-dismiss alerts after 5 seconds (exclude alerts inside modals or marked permanent)
    const alerts = document.querySelectorAll('.alert:not(.alert-permanent):not(.modal .alert)');
    alerts.forEach(function (alert) {
        setTimeout(function () {
            const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            bsAlert.close();
        }, 5000);
    });

    // Form validation
    const forms = document.querySelectorAll('.needs-validation');
    forms.forEach(function (form) {
        form.addEventListener('submit', function (event) {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }
            form.classList.add('was-validated');
        });
    });

    // Global Application Confirmation Modal Helper
    window.showAppConfirm = function (message, onConfirm, title = 'Confirm Action', confirmClass = 'btn-danger', confirmText = 'Confirm') {
        const modalEl = document.getElementById('appGlobalConfirmModal');
        if (!modalEl) {
            if (confirm(message)) onConfirm();
            return;
        }
        const titleEl = document.getElementById('appGlobalConfirmModalLabel');
        const bodyEl = document.getElementById('appGlobalConfirmModalBody');
        const btnEl = document.getElementById('appGlobalConfirmModalBtn');

        if (titleEl) titleEl.textContent = title;
        if (bodyEl) bodyEl.textContent = message;
        if (btnEl) {
            btnEl.className = 'btn btn-sm fw-semibold ' + confirmClass;
            btnEl.textContent = confirmText;
            const newBtn = btnEl.cloneNode(true);
            btnEl.parentNode.replaceChild(newBtn, btnEl);
            newBtn.addEventListener('click', function () {
                const modalInstance = bootstrap.Modal.getInstance(modalEl);
                if (modalInstance) modalInstance.hide();
                onConfirm();
            });
        }

        const modalInstance = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
        modalInstance.show();
    };

    // Global Application Toast Helper
    window.showAppToast = function (message, type = 'info', title = null) {
        let container = document.getElementById('appToastContainer');
        if (!container) {
            container = document.createElement('div');
            container.id = 'appToastContainer';
            container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
            container.style.zIndex = '1099';
            document.body.appendChild(container);
        }

        const typeIcons = {
            'success': 'bi-check-circle-fill text-success',
            'danger': 'bi-exclamation-triangle-fill text-danger',
            'error': 'bi-exclamation-triangle-fill text-danger',
            'warning': 'bi-exclamation-circle-fill text-warning',
            'info': 'bi-info-circle-fill text-primary'
        };

        const typeTitles = {
            'success': 'Success',
            'danger': 'Error',
            'error': 'Error',
            'warning': 'Warning',
            'info': 'Notice'
        };

        const headerTitle = title || typeTitles[type] || 'Notice';
        const iconClass = typeIcons[type] || 'bi-info-circle-fill text-primary';

        const toastEl = document.createElement('div');
        toastEl.className = 'toast border-0 shadow-lg rounded-3';
        toastEl.setAttribute('role', 'alert');
        toastEl.setAttribute('aria-live', 'assertive');
        toastEl.setAttribute('aria-atomic', 'true');
        toastEl.innerHTML = `
            <div class="toast-header border-0 bg-white">
                <i class="bi ${iconClass} me-2 fs-6"></i>
                <strong class="me-auto text-dark">${headerTitle}</strong>
                <small class="text-muted">Just now</small>
                <button type="button" class="btn-close ms-2" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body pt-1 pb-3 text-secondary small">
                ${message}
            </div>
        `;

        container.appendChild(toastEl);
        const bsToast = new bootstrap.Toast(toastEl, { delay: 4500 });
        toastEl.addEventListener('hidden.bs.toast', function () {
            toastEl.remove();
        });
        bsToast.show();
    };

    // Confirm actions with custom modal
    const deleteButtons = document.querySelectorAll('[data-confirm]');
    deleteButtons.forEach(function (button) {
        button.addEventListener('click', function (event) {
            if (button.dataset.confirmed === 'true') {
                button.dataset.confirmed = 'false';
                return;
            }
            event.preventDefault();
            const message = button.getAttribute('data-confirm') || 'Are you sure you want to proceed?';
            const form = button.closest('form');
            window.showAppConfirm(message, function () {
                if (form) {
                    button.dataset.confirmed = 'true';
                    if (button.type === 'submit') {
                        button.click();
                    } else {
                        form.submit();
                    }
                } else if (button.href) {
                    window.location.href = button.href;
                }
            });
        });
    });

    // Currency formatting helper
    window.formatCurrency = function (amount, symbol = '$') {
        return symbol + parseFloat(amount).toFixed(2).replace(/\d(?=(\d{3})+\.)/g, '$&,');
    };

    // Initialize tooltips
    const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl));

    // Sync Manager Bindings
    const syncBtn = document.getElementById('sync-container');
    if (syncBtn) {
        syncBtn.addEventListener('click', function () {
            if (window.syncManager) {
                window.syncManager.triggerSync();
            }
        });
    }
});
