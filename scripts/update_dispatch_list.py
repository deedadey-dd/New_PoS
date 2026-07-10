import re

with open('templates/sales/dispatch_list.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Change Title
content = content.replace('{% block title %}Sales History{% endblock %}', '{% block title %}Dispatch History{% endblock %}')
content = content.replace('<h2 class="mb-1">Sales History</h2>', '<h2 class="mb-1">Dispatch History</h2>')
content = content.replace('<p class="text-muted mb-0">View and manage sales transactions</p>', '<p class="text-muted mb-0">View all dispatched sales and goods</p>')

# Remove Open POS button
content = re.sub(r'{% if role_name != \'AUDITOR\' %}\s*<a href="{% url \'sales:pos\' %}" class="btn btn-primary">\s*<i class="bi bi-shop me-2"></i>Open POS\s*</a>\s*{% endif %}', '', content)

# Simplify Filters
filters_start = content.find('<!-- Filters -->')
filters_end = content.find('<div class="card">', filters_start)
new_filters = """<!-- Filters -->
<div class="card mb-4">
    <div class="card-body">
        <form method="get" class="row g-3">
            <div class="col-md-4">
                <label class="form-label">Search</label>
                <div class="input-group">
                    <span class="input-group-text bg-white border-end-0 text-muted"><i class="bi bi-search"></i></span>
                    <input type="text" name="q" class="form-control border-start-0 ps-0" placeholder="Invoice #, Customer Name, Phone..." value="{{ q }}">
                </div>
            </div>
            
            <div class="col-md-3">
                <label class="form-label">From Date</label>
                <input type="date" name="date_from" class="form-control" value="{{ date_from }}">
            </div>
            <div class="col-md-3">
                <label class="form-label">To Date</label>
                <input type="date" name="date_to" class="form-control" value="{{ date_to }}">
            </div>
            <div class="col-md-2 d-flex align-items-end gap-2">
                <button type="submit" class="btn btn-primary flex-grow-1">
                    <i class="bi bi-filter me-1"></i>Filter
                </button>
                <a href="?" class="btn btn-outline-secondary">
                    <i class="bi bi-x-lg"></i>
                </a>
            </div>
        </form>
    </div>
</div>
"""
content = content[:filters_start] + new_filters + content[filters_end:]

# Table Headers
headers_start = content.find('<thead>')
headers_end = content.find('</thead>', headers_start) + 8
new_headers = """<thead>
                    <tr>
                        <th>{% sort_link "Sale #" "sale_number" %}</th>
                        <th>{% sort_link "Date" "created_at" %}</th>
                        <th>Shop</th>
                        <th>Customer</th>
                        <th>{% sort_link "Attendant" "attendant__username" %}</th>
                        <th>Dispatch Status</th>
                        <th class="text-end">Actions</th>
                    </tr>
                </thead>"""
content = content[:headers_start] + new_headers + content[headers_end:]

# Table Body row (remove Payment, Total, Status, add Dispatch Status)
# Instead of complex regex, I will write a simple python script to parse and replace the table row
row_regex = re.compile(r'<tr>(.*?)</tr>', re.DOTALL)
def replace_row(match):
    row = match.group(1)
    if 'sale.sale_number' not in row:
        return match.group(0) # Not the data row
    
    # We'll just manually replace the entire tbody contents since it's simpler
    return match.group(0)

# Actually, it's easier to just replace the whole tbody inner
tbody_start = content.find('<tbody>') + 7
tbody_end = content.find('</tbody>', tbody_start)
new_tbody = """
                    {% for sale in sales %}
                    <tr>
                        <td>
                            <a href="#" class="fw-bold text-decoration-none sale-detail-link"
                               data-sale-id="{{ sale.pk }}">
                                {{ sale.sale_number }}
                            </a>
                        </td>
                        <td>{{ sale.created_at|date:"M d, Y H:i" }}</td>
                        <td>{{ sale.shop.name }}</td>
                        <td>
                            {% if sale.customer %}
                                <span class="badge bg-light text-dark border" data-bs-toggle="tooltip" title="Registered Customer">
                                    <i class="bi bi-person-check-fill text-success me-1"></i>{{ sale.customer.name }}
                                </span>
                            {% elif sale.customer_name %}
                                <span class="badge bg-light text-dark border" data-bs-toggle="tooltip" title="Walk-in Customer">
                                    <i class="bi bi-person-fill text-muted me-1"></i>{{ sale.customer_name }}
                                </span>
                            {% else %}
                                <span class="badge bg-light text-dark border" data-bs-toggle="tooltip" title="Walk-in Customer">
                                    <i class="bi bi-person-fill text-muted me-1"></i>Walk-in Customer
                                </span>
                            {% endif %}
                        </td>
                        <td>{{ sale.attendant.get_full_name|default:sale.attendant.email }}</td>
                        <td>
                            {% if sale.is_dispatched %}
                            <span class="badge bg-success">Fully Dispatched</span>
                            {% else %}
                            <span class="badge bg-warning">Partially Dispatched</span>
                            {% endif %}
                        </td>
                        <td class="text-end">
                            <a href="#" class="btn btn-sm btn-outline-info sale-detail-link"
                               data-sale-id="{{ sale.pk }}"
                               data-bs-toggle="tooltip" title="View Dispatch Details">
                                <i class="bi bi-eye"></i>
                            </a>
                        </td>
                    </tr>
                    {% endfor %}
"""
content = content[:tbody_start] + new_tbody + content[tbody_end:]

# Empty state
content = content.replace('<h4 class="mt-3">No Sales Yet</h4>', '<h4 class="mt-3">No Dispatches Found</h4>')
content = content.replace('<p class="text-muted">Sales will appear here once you start using the POS.</p>', '<p class="text-muted">Dispatched sales will appear here.</p>')

# Remove Payment Modal
pm_start = content.find('<!-- Payment Modal -->')
if pm_start != -1:
    content = content[:pm_start]

content += "{% endblock %}\n\n{% block extra_js %}\n<script>\n"

js_script = """
document.addEventListener('DOMContentLoaded', function() {
    const modal = new bootstrap.Modal(document.getElementById('saleDetailModal'));
    const loadingEl = document.getElementById('saleDetailLoading');
    const contentEl = document.getElementById('saleDetailContent');
    const printBtn = document.getElementById('saleDetailPrintBtn');
    const currencySymbol = '{{ current_tenant.currency_symbol|default:"$" }}';

    document.querySelectorAll('.sale-detail-link').forEach(function(link) {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const saleId = this.dataset.saleId;

            // Show loading, hide content
            loadingEl.style.display = 'block';
            contentEl.style.display = 'none';

            // Set print button link
            if(printBtn) printBtn.href = '/sales/' + saleId + '/receipt/';

            // Open modal immediately
            modal.show();

            // Fetch sale detail
            fetch('/sales/api/' + saleId + '/detail/')
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    renderSaleDetail(data);
                    
                    const dispatchBtn = document.getElementById('saleDetailDispatchBtn');
                    if (dispatchBtn) {
                        if (data.status_code === 'PENDING_DISPATCH' || (data.status_code === 'COMPLETED' && !data.is_dispatched)) {
                            dispatchBtn.style.display = 'inline-block';
                            dispatchBtn.dataset.saleId = saleId;
                            dispatchBtn.dataset.saleNumber = data.sale_number;
                            _dispatchSaleItems = data.items;  // store for modal
                            window._dispatchHistory = data.dispatch_history;
                        } else {
                            dispatchBtn.style.display = 'none';
                        }
                    }
                    
                    loadingEl.style.display = 'none';
                    contentEl.style.display = 'block';
                })
                .catch(function(err) {
                    contentEl.innerHTML = '<div class="alert alert-danger">Failed to load sale details.</div>';
                    loadingEl.style.display = 'none';
                    contentEl.style.display = 'block';
                });
        });
    });

    function getStatusBadge(statusCode, statusDisplay) {
        const colors = {
            'COMPLETED': 'success',
            'VOIDED': 'danger',
            'PENDING': 'warning',
        };
        const color = colors[statusCode] || 'secondary';
        return '<span class="badge bg-' + color + '">' + statusDisplay + '</span>';
    }

    function getPaymentBadge(code, display) {
        const colors = {
            'CASH': 'success',
            'ECASH': 'primary',
            'CREDIT': 'warning',
        };
        const color = colors[code] || 'secondary';
        return '<span class="badge bg-' + color + '">' + display + '</span>';
    }

    function renderSaleDetail(data) {
        let html = '';

        // Header info
        html += '<div class="row mb-3">';
        html += '<div class="col-sm-6">';
        html += '<h6 class="text-muted mb-1">Sale Number</h6>';
        html += '<p class="fw-bold fs-5 mb-2">' + data.sale_number + '</p>';
        html += '</div>';
        html += '<div class="col-sm-6 text-sm-end">';
        html += '<h6 class="text-muted mb-1">Status</h6>';
        html += '<p class="mb-2">' + getStatusBadge(data.status_code, data.status) + '</p>';
        html += '</div>';
        html += '</div>';

        // Details row
        html += '<div class="row mb-3 py-2 bg-light rounded">';
        html += '<div class="col-sm-4">';
        html += '<small class="text-muted d-block">Date</small>';
        html += '<span class="fw-medium">' + data.created_at + '</span>';
        html += '</div>';
        html += '<div class="col-sm-4">';
        html += '<small class="text-muted d-block">Shop</small>';
        html += '<span class="fw-medium">' + data.shop + '</span>';
        html += '</div>';
        html += '<div class="col-sm-4">';
        html += '<small class="text-muted d-block">Attendant</small>';
        html += '<span class="fw-medium">' + data.attendant + '</span>';
        html += '</div>';
        html += '</div>';

        // Customer and payment
        html += '<div class="row mb-3">';
        html += '<div class="col-sm-6">';
        html += '<small class="text-muted d-block">Payment Method</small>';
        html += getPaymentBadge(data.payment_method_code, data.payment_method);
        html += '</div>';
        if (data.customer) {
            html += '<div class="col-sm-6">';
            html += '<small class="text-muted d-block">Customer</small>';
            let customerHtml = data.customer;
            if (data.customer_id) {
                customerHtml = '<a href="/customers/' + data.customer_id + '/" class="text-decoration-none fw-bold">' + data.customer + '</a>';
            }
            html += '<span class="fw-medium">' + customerHtml + '</span>';
            if (data.customer_phone) {
                html += '<br><small class="text-muted"><i class="bi bi-telephone me-1"></i>' + data.customer_phone + '</small>';
            }
            html += '</div>';
        }
        html += '</div>';

        // Items table
        html += '<h6 class="border-bottom pb-2 mb-0">Items</h6>';
        html += '<div class="table-responsive">';
        html += '<table class="table table-sm mb-0">';
        html += '<thead class="table-light"><tr>';
        html += '<th>Product</th><th class="text-center">Qty</th>';
        html += '<th class="text-center">Dispatched</th>';
        html += '<th class="text-end">Price</th><th class="text-end">Total</th>';
        html += '</tr></thead><tbody>';

        data.items.forEach(function(item) {
            html += '<tr>';
            html += '<td>' + item.product_name + '<br><small class="text-muted">' + item.sku + '</small></td>';
            html += '<td class="text-center">' + parseFloat(item.quantity) + '</td>';
            let disp = parseFloat(item.dispatched_quantity);
            if(item.is_fully_dispatched) {
                html += '<td class="text-center text-success fw-bold">' + disp + ' <i class="bi bi-check-circle-fill"></i></td>';
            } else {
                html += '<td class="text-center">' + disp + '</td>';
            }
            html += '<td class="text-end">' + currencySymbol + parseFloat(item.unit_price).toFixed(2) + '</td>';
            html += '<td class="text-end">' + currencySymbol + parseFloat(item.total).toFixed(2) + '</td>';
            html += '</tr>';
        });

        html += '</tbody></table></div>';
        
        // Dispatch History timeline inside modal if any
        if (data.dispatch_history && data.dispatch_history.length > 0) {
            html += '<h6 class="border-bottom pb-2 mt-4 mb-2"><i class="bi bi-truck me-2"></i>Dispatch Log</h6>';
            data.dispatch_history.forEach(function(event, idx) {
                const itemRows = event.items.map(i =>
                    `<tr>
                        <td class="ps-3 text-muted small">${i.product_name}</td>
                        <td class="text-center small fw-medium">${i.qty}</td>
                    </tr>`
                ).join('');

                html += `
                <div class="border rounded-3 mb-2 overflow-hidden">
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
                    </table>
                </div>`;
            });
        }

        // Totals
        html += '<div class="border-top pt-2 mt-4">';
        html += '<div class="d-flex justify-content-between fw-bold fs-5 border-top pt-2">';
        html += '<span>Total Paid</span><span>' + currencySymbol + parseFloat(data.amount_paid).toFixed(2) + '</span>';
        html += '</div></div>';

        contentEl.innerHTML = html;
    }
});
</script>
{% include 'sales/partials/partial_dispatch_js.html' %}
{% endblock %}
"""
content += js_script

with open('templates/sales/dispatch_list.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated dispatch_list.html successfully")
