# POS System Documentation

This directory contains documentation for the POS System.

## User Documentation

| Document | Description |
|----------|-------------|
| [USER_MANUAL.md](USER_MANUAL.md) | Comprehensive user guide covering all system features |

## Quick Links

### For Shop Staff
- Opening/Closing Shifts → See [Point of Sale section](USER_MANUAL.md#point-of-sale-pos)
- Making Sales & POS Stock Availability → See [Making a Sale](USER_MANUAL.md#making-a-sale)
- Strict Workflow Invoicing & Reserved Goods → See [Strict Sales Workflow: POS & Invoicing](USER_MANUAL.md#strict-sales-workflow--inventory-models)
- Requesting Refunds (Cashier in Strict Mode) → See [Requesting a Refund](USER_MANUAL.md#1-requesting-a-refund)
- Disbursing Approved Refunds (Cashier) → See [Cashier Refund Disbursement](USER_MANUAL.md#4-cashier-refund-disbursement--till-safeguards)

### For Managers
- Inventory Management & Location Scoping → See [Inventory Management](USER_MANUAL.md#inventory-management)
- Pending Dispatch KPI on Products → See [Product Detail Scoping](USER_MANUAL.md#product-detail-view--role-scoping)
- Stock Transfers & Restocking → See [Stock Transfers](USER_MANUAL.md#stock-transfers)
- Dispatch & Halting Orders → See [Strict Workflow: Dispatch Halting](USER_MANUAL.md#2-strict-workflow-dispatch-halting--reconciliation)
- Dispatch Physical Stock Guards → See [Physical Stock Dispatch Guard](USER_MANUAL.md#physical-stock-dispatch-guard)
- Reports → See [Reports & Analytics](USER_MANUAL.md#reports--analytics)

### For Accountants & Admins
- User Management → See [Settings & Administration](USER_MANUAL.md#settings--administration)
- Global vs. Shop Inventory Settings → See [Inventory Visibility & Strict Sales Workflow Settings](#inventory-visibility--strict-sales-workflow-settings)
- Stock Adjustments & Auditor Bulletins → See [Stock Adjustments & Approval Workflow](USER_MANUAL.md#stock-adjustments)
- Approving & Cancelling Refund Requests → See [Refund Approval & Cancellation](USER_MANUAL.md#3-refund-approval--cancellation-accountant--admin)
- End of Day Reconciliation & Multi-Channel History → See [Refunds in Reports & History Pages Across All Channels](USER_MANUAL.md#5-refunds-in-reports--history-pages-across-all-channels)

---

## Inventory Visibility & Strict Sales Workflow Settings

The system provides three synchronized configuration levers to balance stock control, catalog presentation, and cash-flow operations across different types of retail shops.

### The Three Configuration Controls

1. **`Allow Negative Stock` (`allow_negative_stock`) &mdash; Global Setting (`/settings/`)**
   - **Enforcement:** Controls whether any transaction in the system (cash checkout, invoice fulfillment, or dispatch) can force physical inventory into negative values.
   - **Recommended Value:** `False`. When set to `False`, the backend prevents dispatching or selling non-existent physical stock, guaranteeing ledger integrity.

2. **`Hide Zero-Stock Products in POS` (`hide_zero_stock_in_pos`) &mdash; Shop Setting (`/sales/shop-settings/`)**
   - **Enforcement:** Filters the POS product catalog and search results.
   - **Behavior:** When enabled, any product whose sellable stock is `0` or negative is hidden from the POS screen so attendants only see and add available items.

3. **`Show Items If Physical Stock Exists (Ignore Undispatched Quantity)` (`show_items_with_physical_stock_in_pos`) &mdash; Shop Setting (`/sales/shop-settings/`)**
   - **Enforcement:** Defines how "sellable stock" is computed in Strict Sales Workflow where paid sales enter `PENDING_DISPATCH` before physical handover.
   - **When Disabled (`False`):** `Sellable Stock = Physical Stock − Pending Dispatch`. Paid items committed to customer orders are strictly reserved.
   - **When Enabled (`True`):** `Sellable Stock = Physical Stock`. Items remain visible and sellable as long as physical units are sitting on the floor, regardless of undispatched orders.

---

### Retail Scenarios & Best Practice Configurations

| Setting / Scenario | Scenario A: Committed-Reserve Model (Unique / Bespoke Goods) | Scenario B: Physical-Floor Model (Replenishable / Fast-Moving Goods) | Scenario C: Backorder / Pre-Sale Model |
| :--- | :--- | :--- | :--- |
| **Typical Businesses** | Furniture, luxury goods, unique electronics, bespoke goods, one-of-a-kind art | Supermarkets, grocery, hardware, building supplies, pharmaceuticals, standard consumer goods | Dropshipping, custom manufacturing, pre-orders |
| **Global `Allow Negative Stock`** | `False` | `False` | `True` |
| **Local `Hide Zero-Stock in POS`** | `True` | `True` | `False` |
| **Local `Show Physical Stock`** | `False` (Respect undispatched quantity) | `True` (Ignore undispatched quantity) | `Any` |
| **Sellable Stock Formula** | `Physical Stock − Pending Dispatch` | `Physical Stock` | Unbounded |
| **POS & Invoicing Behavior** | Once an item is paid for and moves to pending dispatch, sellable stock drops. When `available <= 0`, product disappears from POS and further invoices are blocked. | Attendants can continue selling and creating invoices as long as physical units remain on the shelf, even if pending orders exceed stock. | Products are always visible and can be sold into negative quantities. |
| **Dispatch & Replenishment** | Goods remain reserved for the buyer. When the customer arrives, the manager dispatches the reserved items. | If floor stock was sold out to walk-in buyers, manager cannot dispatch into negative stock. Manager fulfills what is available, and requests a **Stock Transfer** to replenish the rest. | Dispatches proceed without stock limits. Balance is reconciled when replenishment is recorded. |

---

## Role-Based Scoping & Audit Accountability

- **Shop Manager Location Scoping:** On the Product Detail page (`/inventory/products/<id>/`), Shop Managers are strictly locked to their assigned shop location. They cannot view batch details, stock valuations, ledger entries, or movements of other shops. HQ roles (`ADMIN`, `STORES_MANAGER`, `ACCOUNTANT`, `AUDITOR`) have location selectors to inspect individual branches or all locations combined.
- **Pending Dispatch KPI:** Product detail pages explicitly separate physical on-hand inventory from items committed to pending customer dispatches.
- **Stock Adjustments & Bulletins:** Any stock adjustment created or reviewed automatically creates notifications and bulletin notices for Accountants and Auditors for immediate financial and audit visibility.

---

## In-App Help

Users can access contextual help within the application by clicking the **?** (question mark) icon in the top navigation bar or navigating to `/help/` or `/documentation/`.

## Technical Documentation

For developers and system administrators, see the codebase README in the project root.

---

*Last Updated: November 2026*
