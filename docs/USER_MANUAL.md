# POS System - User Manual

## Table of Contents

1. [Introduction](#introduction)
2. [Getting Started](#getting-started)
3. [User Roles & Permissions](#user-roles--permissions)
4. [Dashboard](#dashboard)
5. [Point of Sale (POS)](#point-of-sale-pos)
6. [Inventory Management](#inventory-management)
7. [Stock Transfers](#stock-transfers)
8. [Stock Requests](#stock-requests)
9. [Customer Management](#customer-management)
10. [Cash Management](#cash-management)
11. [Reports & Analytics](#reports--analytics)
12. [Tracking & Audit Features](#tracking--audit-features)
13. [Settings & Administration](#settings--administration)
14. [Troubleshooting](#troubleshooting)

---

## Introduction

Welcome to the POS (Point of Sale) System! This comprehensive system helps you manage:

- **Point of Sale operations** - Process sales, manage payments, and issue receipts
- **Inventory tracking** - Track products, batches, and stock levels across locations
- **Stock transfers** - Move inventory between Production, Stores, and Shops
- **Customer accounts** - Manage customer credit and payment history
- **Financial tracking** - Monitor cash flow, sales reports, and profit/loss

### System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                          ADMIN                                   │
│                    (Full system control)                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
    ┌────────────────────────┼────────────────────────────────┐
    │                        │                                │
    ▼                        ▼                                ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐
│  PRODUCTION  │───►│    STORES    │───►│        SHOPS          │
│   MANAGER    │    │    MANAGER   │    │  Manager + Attendants │
└──────────────┘    └──────────────┘    └──────────────────────┘
        │                   │                       │
        └───────────────────┴───────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
        ┌───────────┐              ┌───────────┐
        │ ACCOUNTANT│              │  AUDITOR  │
        │(Financial)│              │(Read-only)│
        └───────────┘              └───────────┘
```

---

## Getting Started

### Logging In

1. Navigate to your POS system URL
2. Enter your **email** and **password**
3. Click **Login**

> **Note:** If this is your first login after password reset, you'll be prompted to change your password.

### First-Time Setup (Admin Only)

If you're the first admin user:
1. Login with your admin credentials
2. You'll be redirected to **Tenant Setup**
3. Enter your organization details:
   - Organization Name
   - Currency Symbol (e.g., GH₵, $, £)
   - Address and contact information
4. Click **Complete Setup**

---

## User Roles & Permissions

| Role | Description | Key Permissions |
|------|-------------|-----------------|
| **Admin** | Full system administrator | All permissions + user management |
| **Production Manager** | Manages production location | Inventory, transfers, batch creation |
| **Stores Manager** | Manages central stores | Inventory, transfers in/out |
| **Shop Manager** | Manages retail shop | POS, sales, local inventory, cash transfers |
| **Shop Attendant** | Front-line sales staff | POS, basic sales only |
| **Accountant** | Financial management | Cash transfers, sales reports, price history |
| **Auditor** | Read-only auditing | View all data, no modifications |

---

## Dashboard

The dashboard provides an at-a-glance view of your business:

### For Shop Staff
- **Today's Sales** - Total sales amount for the day
- **Pending Transfers** - Incoming stock awaiting receipt

### For Managers & Admin
- **Total Locations** - Number of locations in your organization
- **Total Users** - Staff count
- **Total Shops** - Retail locations
- **Pending Incoming Transfers** - Stock sent to your location awaiting receipt
- **Disputed Transfers** - Transfers with quantity discrepancies

### Quick Actions
- Click on **Pending Transfers** badge to view and receive stock
- Click on **Cash on Hand** badge to manage cash transfers

---

## Point of Sale (POS)

### Opening a Shift

Before making sales, you must open a shift:

1. Click **Point of Sale** in the sidebar
2. If no shift is open, you'll see "Open Shift" prompt
3. Enter the **Opening Cash** amount (cash in the drawer)
4. Click **Open Shift**

### Making a Sale

1. **Search for products:**
   - Type in the search bar (supports product name or SKU)
   - Click a product to add it to the cart

2. **Adjust quantities:**
   - Use **+** / **-** buttons to change quantity
   - Click the **trash icon** to remove an item

3. **Apply discounts (if permitted):**
   - Enter discount percentage or amount

4. **Complete the sale:**
   - Click **Checkout**
   - Enter the **amount received**
   - Select **payment method**:
     - 💵 **Cash** - Traditional cash payment
     - 📱 **E-Cash (Mobile Money)** - Electronic payment
     - 💳 **Credit** - Customer credit (must select customer)
   - Click **Complete Sale**

5. **Print receipt:**
   - A receipt will be generated automatically
   - Click **Print** or share via WhatsApp

### Payment Methods

| Method | Description | Requirements |
|--------|-------------|--------------|
| **Cash** | Physical cash payment | None |
| **E-Cash** | Mobile money (Paystack) | Paystack integration enabled |
| **Credit** | Customer owes the amount | Customer must be selected |
| **Mixed** | Partial cash + credit | Customer must be selected |

### Voiding a Sale

If a sale was made in error:

1. Go to **Sales History**
2. Find the sale
3. Click **View Details**
4. Click **Void Sale** (if permitted)
5. Confirm the action

> **Warning:** Voiding returns stock to inventory automatically.

### Closing a Shift

At the end of your shift:

1. Click **Close Shift** button
2. Enter the **Counted Cash** (actual cash in drawer)
3. The system will compare with expected cash
4. Any discrepancy will be highlighted
5. Click **Close Shift**

---

## Inventory Management

### Products

#### Viewing Products
1. Go to **Inventory → Products**
2. Use filters to find products:
   - Search by name or SKU
   - Filter by category
   - Filter by low stock

#### Creating a Product
1. Click **Add Product**
2. Fill in details:
   - **SKU** - Unique product code
   - **Name** - Product name
   - **Category** - Product category
   - **Unit of Measure** - Pieces, Kg, Liters, etc.
   - **Default Selling Price** - Standard retail price
   - **Reorder Level** - Alert threshold for low stock
3. Click **Save**

### Categories

Organize products into categories:
1. Go to **Inventory → Categories**
2. Click **Add Category**
3. Enter category name
4. Click **Save**

### Batches

Batches track specific lots of products with:
- **Batch Number** - Unique identifier
- **Unit Cost** - Cost per item (for profit tracking)
- **Expiry Date** - For perishable goods
- **Quantity** - Amount in this batch

#### Creating a Batch (Stock In)
1. Go to **Inventory → Batches**
2. Click **Add Batch**
3. Select product and location
4. Enter batch details
5. Click **Save**

> **Tip:** Batches are created at Production or Stores and transferred to Shops.

### Stock Overview

View stock levels across all locations:
1. Go to **Inventory → Stock Overview**
2. See quantities per product per location
3. Identify which locations need restocking

### Shop Pricing

Set location-specific prices (different from default):
1. Go to **Inventory → Shop Pricing**
2. Click **Add Price**
3. Select product, location, and price
4. Click **Save**

---

## Stock Transfers

Transfers move stock between locations (e.g., Stores → Shop).

### Transfer Workflow

```
DRAFT → SENT → RECEIVED (or PARTIAL → DISPUTED → CLOSED)
```

### Creating a Transfer

1. Go to **Transfers**
2. Click **New Transfer**
3. Select:
   - **Source Location** - Where stock is coming from
   - **Destination Location** - Where stock is going
4. Add items:
   - Search for products
   - Enter quantities to send
5. Click **Save as Draft** or **Send Transfer**

### Sending a Transfer

1. Open a draft transfer
2. For each item, enter **Quantity Sent**
3. Click **Send Transfer**
4. The destination will be notified

### Receiving a Transfer

When stock arrives at your location:
1. You'll see a notification badge
2. Go to **Transfers**
3. Find transfers with status **SENT**
4. Click **Receive**
5. For each item:
   - Verify actual quantity received
   - Enter **Quantity Received**
6. Click **Complete Receipt**

### Handling Discrepancies

If received quantity doesn't match sent quantity:
1. Enter the actual quantity received
2. The transfer status becomes **PARTIAL** or **DISPUTED**
3. Add notes explaining the discrepancy
4. The sender can investigate

### Transfer Statuses

| Status | Meaning |
|--------|---------|
| **Draft** | Not yet sent, can be edited |
| **Sent** | In transit to destination |
| **Received** | Fully received, quantities match |
| **Partial** | Some items received, others pending |
| **Disputed** | Quantity discrepancy reported |
| **Closed** | Dispute resolved |
| **Cancelled** | Transfer voided |

---

## Stock Requests

Shops can request stock from Stores:

### Creating a Request

1. Go to **Stock Requests**
2. Click **New Request**
3. Select requesting location
4. Add products and quantities needed
5. Add justification notes
6. Click **Submit Request**

### Approving Requests (Stores Manager)

1. View pending requests
2. Review requested items
3. Click **Approve** or **Reject**
4. If approved, a transfer is automatically created

---

## Customer Management

### Adding Customers

1. Go to **Customers**
2. Click **Add Customer**
3. Enter details:
   - Name
   - Phone number
   - Email (optional)
   - Address (optional)
4. Click **Save**

### Credit Sales

To sell on credit:
1. In POS, select the customer
2. Complete the sale with **Credit** payment method
3. The amount is added to customer's debt

### Recording Payments

When a customer pays their debt:
1. Go to **Customers**
2. Find the customer
3. Click **Record Payment**
4. Enter amount paid
5. Click **Save**

### Viewing Customer History

- See all purchases made by a customer
- View outstanding debt
- Track payment history

---

## Cash Management

### Cash on Hand

Your current cash is shown in the top navigation bar:
- **Green badge** shows available cash
- Click to manage transfers

### Creating a Cash Transfer

When handing over cash (e.g., Shop Manager → Accountant):

1. Go to **Cash Transfers**
2. Click **New Transfer**
3. Select recipient (Accountant)
4. Enter amount
5. Add notes if needed
6. Click **Submit**

### Confirming Cash Received

1. View pending transfers to you
2. Click **Confirm** when cash is received
3. The transfer is completed

### Cash Transfer Statuses

| Status | Meaning |
|--------|---------|
| **Pending** | Awaiting confirmation from recipient |
| **Confirmed** | Successfully transferred |
| **Cancelled** | Transfer voided |

---

## Reports & Analytics

### Sales Report (Shop Manager)

View sales performance for your shop:
1. Go to **Sales Report**
2. Select date range
3. View:
   - Total sales
   - Sales breakdown by product
   - Sales by hour

### Accountant Reports

The Accountant Dashboard shows:
- **Revenue by period**
- **Sales by payment method**
- **Cash vs E-Cash breakdown**
- **Outstanding credit**

### Price History

Track product price changes:
1. Go to **Financial → Price History**
2. Search for a product
3. View all price changes with dates and who made them

---

## Tracking & Audit Features

*Available to: AUDITOR, ACCOUNTANT, ADMIN*

### Product Lifecycle

Track a product's journey through the system:

1. Go to **Tracking & Reports → Product Lifecycle**
2. Select a product
3. View:
   - **Entries** - Stock In, Production, Transfers In, Returns
   - **Exits** - Sales, Transfers Out, Damage/Write-offs
   - **Stock by Location** - Current quantities per location
   - **Detailed Ledger** - Every movement with timestamps

### Profit & Loss by Product

Analyze profitability per product:

1. Go to **Tracking & Reports → P&L by Product**
2. Select time period (Week/Month/Quarter/Year/All)
3. View for each product:
   - Quantity Sold
   - Revenue
   - Cost
   - Gross Profit
   - Margin %

> **Tip:** Products with low margins may need price adjustments.

### Profit & Loss by Location

Compare shop performance:

1. Go to **Tracking & Reports → P&L by Location**
2. View for each shop:
   - Transaction count
   - Revenue
   - Cost
   - Profit
   - Average sale value

### Profit & Loss by Manager

Evaluate staff performance:

1. Go to **Tracking & Reports → P&L by Manager**
2. View for each staff member:
   - Number of sales
   - Total revenue generated
   - Profit contribution
   - Average sale value

### Inventory Movement Report

View all stock movements with filters:

1. Go to **Tracking & Reports → Inventory Movements**
2. Filter by:
   - Transaction type (Sale, Transfer, Damage, etc.)
   - Location
   - Date range
   - Product name/SKU
3. View summary by transaction type
4. See detailed ledger entries

---

## Settings & Administration

### General Settings (Admin Only)

1. Go to **Settings → General**
2. Update organization details:
   - Organization name
   - Currency symbol
   - Contact information

### Payment Provider (Admin Only)

Configure Paystack for E-Cash payments:
1. Go to **Settings → Payment Provider**
2. Enter Paystack API keys
3. Enable/disable E-Cash payments

### User Management (Admin Only)

#### Creating Users
1. Go to **Users**
2. Click **Add User**
3. Enter:
   - Email
   - Name
   - Role
   - Assigned Location
4. Set initial password
5. Click **Save**

> **Note:** Users must change their password on first login.

#### Resetting Passwords
1. Go to **Users**
2. Find the user
3. Click **Reset Password**
4. Enter new password
5. User will be prompted to change it on next login

### Location Management (Admin Only)

#### Creating Locations
1. Go to **Locations**
2. Click **Add Location**
3. Enter:
   - Name
   - Type (Production, Stores, Shop)
   - Address
4. Click **Save**

---

## Troubleshooting

### Common Issues

#### "Cannot complete sale - insufficient stock"
- Check stock levels for the product
- Create a transfer to bring more stock
- Verify the correct batch is selected

#### "Shift must be open to make sales"
- Click **Open Shift** button
- Enter opening cash amount

#### "Cannot void sale - status not allowed"
- Only completed sales can be voided
- Sales older than 24 hours may not be voidable
- Check with Admin for override

#### "Transfer stuck in SENT status"
- The destination hasn't received it yet
- Contact the destination location
- Check if they've logged in

#### Products not showing in POS
- Verify product is marked as **Active**
- Check if product has stock in your location
- Verify batch isn't expired

### Getting Help

If you encounter issues:
1. Check this manual first
2. Contact your Administrator
3. Note any error messages for troubleshooting

---

## Keyboard Shortcuts (POS)

| Shortcut | Action |
|----------|--------|
| `Enter` | Complete sale / Confirm action |
| `Escape` | Cancel / Close modal |
| `/` | Focus search bar |

---

## Glossary

| Term | Definition |
|------|------------|
| **Batch** | A lot of products with shared characteristics (cost, expiry) |
| **SKU** | Stock Keeping Unit - unique product identifier |
| **Tenant** | Your organization within the system |
| **Ledger** | Historical record of all inventory movements |
| **COGS** | Cost of Goods Sold - the cost value of items sold |
| **Margin** | Profit as a percentage of revenue |
| **Shift** | A working period for a sales attendant |

---

## Quick Reference Card

### Daily Tasks - Shop Attendant
1. ☐ Open shift (enter opening cash)
2. ☐ Process sales throughout the day
3. ☐ Receive any pending transfers
4. ☐ Close shift (count cash, reconcile)

### Daily Tasks - Shop Manager
1. ☐ Review shift reports
2. ☐ Check low stock alerts
3. ☐ Create stock requests if needed
4. ☐ Submit cash transfers to Accountant
5. ☐ Review shop sales report

### Weekly Tasks - Accountant
1. ☐ Confirm all pending cash transfers
2. ☐ Review sales reports across shops
3. ☐ Check customer credit balances
4. ☐ Generate profit/loss reports

### Monthly Tasks - Admin
1. ☐ Review P&L by product
2. ☐ Review P&L by location
3. ☐ Review P&L by manager
4. ☐ Adjust pricing if needed
5. ☐ Review user access and roles

---

*Last Updated: January 2026*
*Version: 1.0*
