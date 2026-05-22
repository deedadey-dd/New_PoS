from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.core.models import FeatureMessage

class Command(BaseCommand):
    help = 'Seeds the database with over 50 default feature messages.'

    def handle(self, *args, **options):
        User = get_user_model()
        admin_user = User.objects.filter(is_superuser=True).first()

        if not admin_user:
            self.stdout.write(self.style.ERROR("No superuser found. Please create a superuser first using 'python manage.py createsuperuser'."))
            return

        messages = [
            {
                'feature_key': 'product_images',
                'title': 'Visual Inventory: Add Pictures to Products',
                'content': '''<h2>Identify Products at a Glance</h2>
<p>Make your Point of Sale experience faster and reduce mistakes by adding images to your products.</p>
<ul>
    <li><strong>Faster Checkout:</strong> Attendants can visually identify items without having to read every label.</li>
    <li><strong>Professional Look:</strong> A visual POS interface looks modern and is easier to train new staff on.</li>
</ul>
<p>Visit the Product Management page to upload photos for your inventory today!</p>'''
            },
            {
                'feature_key': 'advanced_search',
                'title': 'Find Items Instantly: Search by SKU or Name',
                'content': '''<h2>Lightning Fast Product Search</h2>
<p>Don't waste time scrolling through hundreds of items. Our POS and inventory screens feature real-time search.</p>
<ul>
    <li><strong>Search by Name:</strong> Just start typing the product name and watch the list filter instantly.</li>
    <li><strong>Search by SKU:</strong> Scan barcodes directly into the search bar or type the Stock Keeping Unit (SKU) to pinpoint exact items.</li>
</ul>
<p>Efficiency is key to keeping checkout lines moving!</p>'''
            },
            {
                'feature_key': 'multi_location_tracking',
                'title': 'Track Inventory Across Multiple Shops',
                'content': '''<h2>A Bird\\'s-Eye View of Your Entire Business</h2>
<p>Running multiple branches? HendAxis PoS makes it effortless to manage stock across all your locations.</p>
<ul>
    <li><strong>Location-Specific Stock:</strong> See exactly how many items are at Shop A versus Shop B.</li>
    <li><strong>Centralized Management:</strong> Update product catalogs once, and have them available everywhere.</li>
</ul>
<p>Stop guessing what's in stock and start knowing with real-time multi-location tracking.</p>'''
            },
            {
                'feature_key': 'stock_transfers',
                'title': 'Seamless Product Transfers Between Locations',
                'content': '''<h2>Move Stock Where It\\'s Needed Most</h2>
<p>Easily manage the flow of goods from your central warehouse to your retail shops, or even between shops.</p>
<ul>
    <li><strong>Digital Dispatch:</strong> Create transfer manifests with just a few clicks.</li>
    <li><strong>Secure Receiving:</strong> Receiving shops must digitally accept the transfer, ensuring accountability.</li>
    <li><strong>Discrepancy Handling:</strong> Easily report shortages or damages during transit.</li>
</ul>
<p>Keep your supply chain tight and your shelves stocked!</p>'''
            },
            {
                'feature_key': 'financial_tracking',
                'title': 'Track Your Finances and Profits Per Product',
                'content': '''<h2>Know Your Bottom Line</h2>
<p>HendAxis PoS isn't just about tracking boxes; it's about tracking your money.</p>
<ul>
    <li><strong>Profit Margins:</strong> See exactly how much profit you make on every single item sold.</li>
    <li><strong>Cost of Goods Sold (COGS):</strong> The system automatically calculates your inventory valuation and COGS based on batch purchasing prices.</li>
</ul>
<p>Make data-driven decisions and identify your most lucrative products!</p>'''
            },
            {
                'feature_key': 'low_stock_alerts',
                'title': 'Never Run Out: Low Stock Alerts',
                'content': '''<h2>Automated Reorder Notifications</h2>
<p>Running out of your best-selling product costs you money. Prevent stockouts with Low Stock Alerts.</p>
<ul>
    <li><strong>Custom Thresholds:</strong> Set a specific "Alert Threshold" for every product (e.g., alert me when we drop below 10 units).</li>
    <li><strong>Dashboard Badges:</strong> Get instant visual notifications on your dashboard when items need reordering.</li>
</ul>
<p>Keep your inventory optimized and your customers happy!</p>'''
            },
            {
                'feature_key': 'batch_expiry_tracking',
                'title': 'Avoid Waste: Batch & Expiry Date Tracking',
                'content': '''<h2>Perfect for Pharmacies and Supermarkets</h2>
<p>Selling perishable goods? Track inventory by batches to ensure you sell the oldest stock first (FIFO).</p>
<ul>
    <li><strong>Expiry Warnings:</strong> The system automatically flags batches that are approaching their expiration dates.</li>
    <li><strong>Cost Tracking:</strong> Different batches can have different purchase costs, ensuring your profit calculations are 100% accurate.</li>
</ul>
<p>Minimize shrinkage and avoid selling expired goods!</p>'''
            },
            {
                'feature_key': 'split_payments',
                'title': 'Flexible Checkout: Split Payments',
                'content': '''<h2>Let Customers Pay How They Want</h2>
<p>Did a customer hand you cash but wants to pay the rest via Mobile Money? No problem!</p>
<ul>
    <li><strong>Multi-Tender Support:</strong> Split a single transaction across Cash, Mobile Money, E-Cash, and Credit seamlessly.</li>
    <li><strong>Accurate Reconciliation:</strong> End-of-day shift reports clearly break down exactly how much of each payment type was received.</li>
</ul>
<p>Never turn away a sale because of payment rigidity again.</p>'''
            },
            {
                'feature_key': 'shift_management',
                'title': 'Accountability First: Shift Management',
                'content': '''<h2>Track Cash Drawers with Precision</h2>
<p>Hold your attendants accountable with structured shift management.</p>
<ul>
    <li><strong>Opening Cash:</strong> Attendants declare their starting float when they open their shift.</li>
    <li><strong>Expected vs Actual:</strong> At closing, the system calculates expected cash based on sales and compares it to the declared actual cash.</li>
    <li><strong>Overage/Shortage Logs:</strong> Any discrepancies are permanently logged for management review.</li>
</ul>
<p>Secure your cash flow today!</p>'''
            },
            {
                'feature_key': 'custom_receipts',
                'title': 'Brand Your Business: Custom Receipts',
                'content': '''<h2>Leave a Lasting Impression</h2>
<p>Your receipt is the last thing your customer sees. Make it count!</p>
<ul>
    <li><strong>Add Your Logo:</strong> Upload your company logo to be printed directly on thermal receipts.</li>
    <li><strong>Custom Headers/Footers:</strong> Add "Thank you for shopping with us!" or your return policy to the bottom of every receipt.</li>
</ul>
<p>Head to Shop Settings to configure your receipt layout.</p>'''
            },
            {
                'feature_key': 'pricing_modes',
                'title': 'Control Your Pricing Strategy',
                'content': '''<h2>Flexible Pricing for Any Business Model</h2>
<p>Whether you want uniform pricing everywhere or localized pricing, we have a mode for you.</p>
<ul>
    <li><strong>Accountant Uniform:</strong> Head office sets one price, and all shops must use it.</li>
    <li><strong>Shop Manager Controlled:</strong> Let local managers set prices competitive to their specific neighborhoods.</li>
</ul>
<p>You can adjust your Pricing Control Mode in the Tenant Settings!</p>'''
            },
            {
                'feature_key': 'auditor_workflow',
                'title': 'Ironclad Security: The Auditor Workflow',
                'content': '''<h2>Stop Inventory Leakage</h2>
<p>When stock goes missing, you need strict oversight. Enter the Auditor role.</p>
<ul>
    <li><strong>Pending Adjustments:</strong> Shop Managers can report damages or lost items, but they cannot delete the stock themselves.</li>
    <li><strong>Auditor Approval:</strong> A designated Auditor must review and approve the adjustment before the system inventory is permanently reduced.</li>
</ul>
<p>Trust, but verify with HendAxis PoS.</p>'''
            },
            {
                'feature_key': 'inventory_ledger',
                'title': 'The Ultimate Audit Trail: Inventory Ledger',
                'content': '''<h2>Track Every Single Movement</h2>
<p>Wondering why an item has 45 units instead of 50? Check the Ledger.</p>
<ul>
    <li><strong>Immutable History:</strong> Every sale, transfer, adjustment, and receipt is logged permanently.</li>
    <li><strong>Time-Stamped:</strong> See exactly who did what, and at what time.</li>
</ul>
<p>The Inventory Ledger is your single source of truth for all stock movements.</p>'''
            },
            {
                'feature_key': 'bulk_excel_upload',
                'title': 'Save Hours: Bulk Excel Uploads',
                'content': '''<h2>Migrate Your Inventory in Minutes</h2>
<p>Adding hundreds of products manually is a pain. That's why we built the Bulk Excel Upload tool.</p>
<ul>
    <li><strong>Download the Template:</strong> Get a pre-formatted Excel file directly from the Products page.</li>
    <li><strong>Upload and Go:</strong> Fill it out, upload it, and watch your entire catalog populate instantly.</li>
</ul>
<p>Perfect for onboarding new stores or adding seasonal catalogs!</p>'''
            },
            {
                'feature_key': 'data_exports',
                'title': 'Your Data, Your Way: Excel Exports',
                'content': '''<h2>Export Everything for Deep Analysis</h2>
<p>Need to run complex pivot tables or share data with your accountant? Export it!</p>
<ul>
    <li><strong>Sales Reports:</strong> Export complete transaction histories.</li>
    <li><strong>Inventory Valuations:</strong> Export current stock levels and their associated financial values.</li>
</ul>
<p>We believe your data belongs to you.</p>'''
            },
            {
                'feature_key': 'favorite_products',
                'title': 'Speed Up Checkout: Favorite Products',
                'content': '''<h2>One-Click Access to Best Sellers</h2>
<p>Do you have a few items that sell constantly? Mark them as Favorites!</p>
<ul>
    <li><strong>Quick Access Grid:</strong> Starred products appear in a special quick-access grid on the POS screen.</li>
    <li><strong>No Searching Required:</strong> Attendants can simply tap the image to add it to the cart instantly.</li>
</ul>
<p>Shave seconds off every transaction.</p>'''
            },
            {
                'feature_key': 'hide_zero_stock',
                'title': 'Declutter Your POS: Hide Zero-Stock Items',
                'content': '''<h2>Keep the Interface Clean</h2>
<p>If you don't have it in stock, why show it to the cashier?</p>
<ul>
    <li><strong>Toggle Switch:</strong> Shop Managers can enable "Hide Zero-Stock Products in POS" from their Shop Settings.</li>
    <li><strong>Faster Browsing:</strong> Attendants only see items they can actually sell today.</li>
</ul>
<p>A cleaner interface means a faster checkout!</p>'''
            },
            {
                'feature_key': 'dark_mode',
                'title': 'Easy on the Eyes: Dark Mode',
                'content': '''<h2>Work Comfortably in Any Lighting</h2>
<p>HendAxis PoS features a stunning, system-wide Dark Theme.</p>
<ul>
    <li><strong>Reduce Eye Strain:</strong> Perfect for late-night shifts or dimly lit restaurant environments.</li>
    <li><strong>Modern Aesthetic:</strong> Enjoy deep purples and sleek dark backgrounds that make your data pop.</li>
</ul>
<p>It's not just beautiful; it's functional.</p>'''
            },
            {
                'feature_key': 'price_change_center',
                'title': 'Stay Informed: Price Change Center',
                'content': '''<h2>Track Every Price Fluctuation</h2>
<p>When the Accountant updates global prices, Shop Managers need to know immediately so they can update shelf tags.</p>
<ul>
    <li><strong>Top Nav Alerts:</strong> Get instant notifications for any price changes affecting your shop.</li>
    <li><strong>Historical Center:</strong> Visit the Price Change Center to view, search, and filter price changes from the last 30 days.</li>
</ul>
<p>Never sell an item at the wrong price again.</p>'''
            },
            {
                'feature_key': 'production_module',
                'title': 'Manufacturing Made Easy: Production Module',
                'content': '''<h2>Convert Raw Materials to Finished Goods</h2>
<p>Do you bake bread, mix chemicals, or assemble kits? The Production module is for you.</p>
<ul>
    <li><strong>Deduct Raw Materials:</strong> Log a production run to automatically deduct flour, sugar, and yeast.</li>
    <li><strong>Add Finished Goods:</strong> The system automatically adds the newly baked bread to your sellable inventory.</li>
</ul>
<p>Track your manufacturing costs seamlessly.</p>'''
            },
            {
                'feature_key': 'supplier_management',
                'title': 'Organize Your Vendors: Supplier Management',
                'content': '''<h2>Keep Supplier Details Handy</h2>
<p>Who did you buy that batch of electronics from, and what is their phone number?</p>
<ul>
    <li><strong>Supplier Directory:</strong> Store names, contact details, and addresses for all your vendors.</li>
    <li><strong>Batch Tracking:</strong> Link incoming stock batches directly to specific suppliers for easy reordering and returns.</li>
</ul>
<p>Manage your relationships as well as your stock.</p>'''
            },
            {
                'feature_key': 'role_based_access',
                'title': 'Secure Your System: Role-Based Access',
                'content': '''<h2>Give Employees Only the Access They Need</h2>
<p>Don't give the keys to the kingdom to everyone.</p>
<ul>
    <li><strong>Attendants:</strong> Can only sell and view their own shift reports.</li>
    <li><strong>Shop Managers:</strong> Can manage their specific shop's inventory but not others.</li>
    <li><strong>Accountants & Auditors:</strong> Have read-only access to financials and can approve adjustments globally.</li>
</ul>
<p>Security is built into the core of HendAxis PoS.</p>'''
            },
            {
                'feature_key': 'real_time_dashboards',
                'title': 'Data at a Glance: Real-Time Dashboards',
                'content': '''<h2>Actionable Insights As They Happen</h2>
<p>Log in and instantly know the health of your business.</p>
<ul>
    <li><strong>Sales Charts:</strong> Beautiful, interactive graphs showing today's revenue vs yesterday's.</li>
    <li><strong>Quick Stats:</strong> See total items sold, total revenue, and total profit updated in real-time.</li>
</ul>
<p>Stop crunching numbers and start reading dashboards.</p>'''
            },
            {
                'feature_key': 'top_selling_analytics',
                'title': 'Know Your Winners: Top Selling Products',
                'content': '''<h2>Double Down on What Works</h2>
<p>Your dashboard automatically calculates and displays your top-performing products.</p>
<ul>
    <li><strong>By Volume:</strong> See what items are flying off the shelves the fastest.</li>
    <li><strong>By Revenue:</strong> See what items are generating the most cash for the business.</li>
</ul>
<p>Use this data to optimize your purchasing strategy.</p>'''
            },
            {
                'feature_key': 'stock_valuation',
                'title': 'Financial Snapshot: Stock Valuation',
                'content': '''<h2>How Much Capital is on Your Shelves?</h2>
<p>For accounting and insurance purposes, you need to know the total value of your inventory.</p>
<ul>
    <li><strong>Automated Calculation:</strong> We multiply your current stock by the specific batch purchase costs.</li>
    <li><strong>Total Value Widget:</strong> View your total inventory asset value right from the Tenant Admin dashboard.</li>
</ul>
<p>Keep your accountants happy and your books balanced.</p>'''
            },
            {
                'feature_key': 'category_management',
                'title': 'Stay Organized: Category Management',
                'content': '''<h2>Group Your Products Logically</h2>
<p>Having 1,000 unorganized products is a nightmare. Categories solve this.</p>
<ul>
    <li><strong>Hierarchy:</strong> Group items into logical buckets like "Electronics", "Beverages", or "Apparel".</li>
    <li><strong>POS Filtering:</strong> Attendants can filter the POS screen by category to find items faster.</li>
</ul>
<p>A clean catalog is a happy catalog.</p>'''
            },
            {
                'feature_key': 'unit_of_measure',
                'title': 'Sell How You Want: Units of Measure',
                'content': '''<h2>Pieces, Kilograms, or Liters?</h2>
<p>Not everything is sold by the "piece".</p>
<ul>
    <li><strong>Custom Units:</strong> Assign units of measure to products so receipts and ledgers read correctly.</li>
    <li><strong>Clear Communication:</strong> Ensure customers know they are buying "2 KG" and not "2 Pieces".</li>
</ul>
<p>Flexibility for all types of retail and wholesale.</p>'''
            },
            {
                'feature_key': 'print_ledger_receipts',
                'title': 'Paper Trails: Print Ledger Receipts',
                'content': '''<h2>Physical Proof for Stock Movements</h2>
<p>Sometimes you just need a piece of paper to file away.</p>
<ul>
    <li><strong>Printable Audit Trail:</strong> Generate a printable A4 receipt for any inventory ledger entry.</li>
    <li><strong>Signature Lines:</strong> Receipts include space for signatures, perfect for documenting manual stock handovers.</li>
</ul>
<p>Bridge the gap between digital tracking and physical compliance.</p>'''
            },
            {
                'feature_key': 'summary_modals',
                'title': 'Quick Insights: Summary Modals',
                'content': '''<h2>Information Without Leaving the Page</h2>
<p>Want to know how a specific Shop is doing without opening a new tab?</p>
<ul>
    <li><strong>Hover & Click:</strong> Click on a location or user in the Superadmin dashboard to open a quick Summary Modal.</li>
    <li><strong>Instant Stats:</strong> View recent activity and key metrics instantly.</li>
</ul>
<p>Navigate faster with AJAX-powered modals.</p>'''
            },
            {
                'feature_key': 'suspend_users',
                'title': 'Access Control: Suspend Users Instantly',
                'content': '''<h2>Protect Your Business in Seconds</h2>
<p>If an employee leaves the company, you need to revoke their access immediately.</p>
<ul>
    <li><strong>One-Click Suspension:</strong> Tenant Admins can toggle a user's active status with a single click.</li>
    <li><strong>Immediate Effect:</strong> Suspended users are immediately logged out and blocked from the POS.</li>
</ul>
<p>Peace of mind at the click of a button.</p>'''
            },
            {
                'feature_key': 'password_reset_admin',
                'title': 'No Hassle IT: Admin Password Resets',
                'content': '''<h2>Help Your Staff Get Back to Work</h2>
<p>Attendant forgot their password? No need to deal with email reset links.</p>
<ul>
    <li><strong>Direct Reset:</strong> Tenant Admins can force-reset any staff member's password directly from the User Management dashboard.</li>
    <li><strong>Security First:</strong> The staff member will be prompted to change this temporary password the first time they log in.</li>
</ul>
<p>Minimize downtime and frustration.</p>'''
            },
            {
                'feature_key': 'forced_password_change',
                'title': 'Security Compliance: Forced Password Changes',
                'content': '''<h2>Protect Default Accounts</h2>
<p>When you create a new user and give them a temporary password like "123456", it's a security risk.</p>
<ul>
    <li><strong>Automatic Enforcement:</strong> HendAxis PoS detects when a user is logging in with a default or admin-reset password.</li>
    <li><strong>Mandatory Update:</strong> They are locked in a screen forcing them to create a secure, private password before accessing the system.</li>
</ul>
<p>Enterprise-grade security for your retail business.</p>'''
            },
            {
                'feature_key': 'tenant_isolation',
                'title': 'Rock Solid Privacy: Multi-Tenant Architecture',
                'content': '''<h2>Your Data is Yours Alone</h2>
<p>Under the hood, HendAxis PoS uses advanced multi-tenant data isolation.</p>
<ul>
    <li><strong>Data Silos:</strong> Your products, sales, and customers are mathematically isolated from every other business on the platform.</li>
    <li><strong>Zero Leakage:</strong> Even in the event of a bug, our database constraints make it impossible for Shop A to see Shop B's data.</li>
</ul>
<p>Sleep easy knowing your trade secrets are safe.</p>'''
            },
            {
                'feature_key': 'email_campaigns',
                'title': 'Stay in the Loop: Feature Updates',
                'content': '''<h2>You\\'re Reading One Right Now!</h2>
<p>We built this exact email delivery system to ensure you always know about the latest tools at your disposal.</p>
<ul>
    <li><strong>Monthly Spotlights:</strong> Every month we highlight a feature that can help you save time or make more money.</li>
    <li><strong>Feedback Loops:</strong> Click the Helpful/Not Helpful buttons below to tell us what you think!</li>
</ul>
<p>We are constantly improving the platform for you.</p>'''
            },
            {
                'feature_key': 'printer_settings',
                'title': 'Hardware Flexibility: Printer Settings',
                'content': '''<h2>Support for Any Setup</h2>
<p>Not all shops have the same hardware. We adapt to you.</p>
<ul>
    <li><strong>Thermal 80mm:</strong> Perfect for standard, fast retail checkout lanes.</li>
    <li><strong>Thermal 58mm:</strong> Great for smaller, budget-friendly portable printers.</li>
    <li><strong>A4 Printers:</strong> Ideal for wholesale operations that need to print full-page invoices.</li>
</ul>
<p>Configure it per-shop in your Shop Settings.</p>'''
            },
            {
                'feature_key': 'contact_support',
                'title': 'We Are Here For You: In-App Support',
                'content': '''<h2>Help is Just a Click Away</h2>
<p>Stuck on a feature or found a bug? Reach out directly from the dashboard.</p>
<ul>
    <li><strong>Contact Form:</strong> Use the dedicated Help & Support page to send messages straight to our engineering team.</li>
    <li><strong>Fast Resolution:</strong> Because you are logged in, we immediately know your Tenant ID and system version, speeding up support times.</li>
</ul>
<p>Your success is our success.</p>'''
            },
            {
                'feature_key': 'subscription_reminders',
                'title': 'No Surprises: Subscription Reminders',
                'content': '''<h2>Keep Your Business Online</h2>
<p>We ensure you never suffer an unexpected service interruption.</p>
<ul>
    <li><strong>Automated Alerts:</strong> Get friendly email reminders 30 days, 7 days, and 1 day before your subscription expires.</li>
    <li><strong>Grace Periods:</strong> Even if you miss a payment, your data is kept safe while your account is temporarily suspended.</li>
</ul>
<p>Manage your billing stress-free.</p>'''
            },
            {
                'feature_key': 'custom_emails',
                'title': 'Communicate with Your Team: Custom Emails',
                'content': '''<h2>Send Announcements to Your Staff</h2>
<p>Tenant Admins have the power to send targeted emails directly through the platform.</p>
<ul>
    <li><strong>Staff Updates:</strong> Need to announce a holiday schedule to all Shop Managers? Draft and send an email directly from the dashboard.</li>
    <li><strong>Scheduled Delivery:</strong> Write it today, and schedule it to send on Friday afternoon.</li>
</ul>
<p>Keep your internal communication centralized.</p>'''
            },
            {
                'feature_key': 'sales_filtering',
                'title': 'Deep Dive: Sales Filtering',
                'content': '''<h2>Analyze Your Historical Sales</h2>
<p>The Sales History view isn't just a list; it's a powerful analysis tool.</p>
<ul>
    <li><strong>Date Ranges:</strong> Quickly filter by Today, This Week, or This Month.</li>
    <li><strong>Payment Methods:</strong> See all sales that were paid via Mobile Money vs Cash.</li>
    <li><strong>Cashier Tracking:</strong> Filter sales by the specific attendant who rang them up.</li>
</ul>
<p>Uncover trends and optimize your operations.</p>'''
            },
            {
                'feature_key': 'stock_adjustments_reasons',
                'title': 'Why Did We Lose Stock? Adjustment Reasons',
                'content': '''<h2>Categorize Your Shrinkage</h2>
<p>When stock is lost, knowing *why* is half the battle.</p>
<ul>
    <li><strong>Categorization:</strong> When making a stock adjustment, staff must select a reason: Damaged, Expired, Theft, or Audit Correction.</li>
    <li><strong>Pattern Recognition:</strong> Over time, you can see if you are losing more money to Expiry or to Damages, and adjust your purchasing accordingly.</li>
</ul>
<p>Turn losses into actionable data.</p>'''
            },
            {
                'feature_key': 'offline_sync_queue',
                'title': 'Bulletproof Reliability: Offline Sync Queue',
                'content': '''<h2>Never Lose a Transaction</h2>
<p>When operating in Offline Mode, what happens to your sales?</p>
<ul>
    <li><strong>Local Storage:</strong> Transactions are securely encrypted and stored in your browser's local memory.</li>
    <li><strong>Background Sync:</strong> As soon as a connection is detected, a background queue silently pushes all sales to the cloud server, one by one.</li>
</ul>
<p>Enterprise reliability, even on unstable networks.</p>'''
            },
            {
                'feature_key': 'quick_add_cart',
                'title': 'Keyboard Shortcuts: Quick Add to Cart',
                'content': '''<h2>Power User Features for Fast Checkout</h2>
<p>Using a laptop instead of a touch screen? You can fly through checkout without a mouse.</p>
<ul>
    <li><strong>Barcode Scanners:</strong> The search bar is optimized for barcode scanners. Scan an item, and it instantly adds to the cart.</li>
    <li><strong>Quantity Adjustment:</strong> Easily click the plus or minus buttons, or manually type a large quantity for bulk sales.</li>
</ul>
<p>Maximize throughput during rush hour.</p>'''
            },
            {
                'feature_key': 'tenant_deactivation',
                'title': 'Platform Management: Superadmin Controls',
                'content': '''<h2>For the Platform Owners</h2>
<p>HendAxis PoS includes a robust Superadmin portal for managing the SaaS platform itself.</p>
<ul>
    <li><strong>Tenant Oversight:</strong> View all active businesses using the platform.</li>
    <li><strong>Subscription Enforcement:</strong> Extend subscriptions, record manual payments, or suspend overdue accounts with a single click.</li>
</ul>
<p>Everything you need to run a software business.</p>'''
            },
            {
                'feature_key': 'superadmin_dashboard',
                'title': 'Global Metrics: Superadmin Dashboard',
                'content': '''<h2>The Pulse of the Platform</h2>
<p>Superadmins get a global view of system health.</p>
<ul>
    <li><strong>Total Revenue:</strong> Track SaaS subscription revenue across all tenants.</li>
    <li><strong>System Usage:</strong> Monitor total active users and locations to gauge server load and platform adoption.</li>
</ul>
<p>Data-driven management for the software provider.</p>'''
            },
            {
                'feature_key': 'demo_mode',
                'title': 'Try Before You Buy: Demo Mode',
                'content': '''<h2>Experience the Full Platform Risk-Free</h2>
<p>We believe our software speaks for itself.</p>
<ul>
    <li><strong>Auto-Login Hub:</strong> Prospective clients can visit the Demo Hub and instantly log in as an Admin, Shop Manager, or Attendant.</li>
    <li><strong>Pre-Populated Data:</strong> The demo environment is filled with realistic products and sales data so you can test reports immediately.</li>
</ul>
<p>Seeing is believing!</p>'''
            },
            {
                'feature_key': 'responsive_design',
                'title': 'Work Anywhere: Mobile Responsive Design',
                'content': '''<h2>Your Business in Your Pocket</h2>
<p>HendAxis PoS isn't just for desktop computers.</p>
<ul>
    <li><strong>Tablet Optimized:</strong> The POS interface is touch-friendly and scales perfectly for iPads and Android tablets.</li>
    <li><strong>Mobile Dashboards:</strong> Check your sales reports and approve stock adjustments directly from your smartphone while on the go.</li>
</ul>
<p>True freedom for the modern business owner.</p>'''
            },
            {
                'feature_key': 'secure_sessions',
                'title': 'Data Security: Secure Sessions',
                'content': '''<h2>Protecting Your Terminal</h2>
<p>If an attendant walks away from the register, your data is safe.</p>
<ul>
    <li><strong>Session Timeouts:</strong> The system will automatically log users out after a period of inactivity.</li>
    <li><strong>CSRF Protection:</strong> All forms and API endpoints are protected against cross-site request forgery attacks.</li>
</ul>
<p>Bank-level security for your retail operations.</p>'''
            },
            {
                'feature_key': 'glassmorphism_ui',
                'title': 'Stunning Aesthetics: Modern UI',
                'content': '''<h2>Software Should Be Beautiful</h2>
<p>We designed HendAxis PoS to be a joy to use every single day.</p>
<ul>
    <li><strong>Glassmorphism:</strong> Enjoy subtle translucency and blur effects that give the interface depth and modern elegance.</li>
    <li><strong>Vibrant Gradients:</strong> Beautiful color palettes replace boring flat colors, making data visualization pop.</li>
</ul>
<p>Upgrade your shop's aesthetic.</p>'''
            },
            {
                'feature_key': 'error_handling',
                'title': 'Smooth Sailing: Graceful Error Handling',
                'content': '''<h2>No More Scary Error Pages</h2>
<p>Things occasionally go wrong, but our software handles it gracefully.</p>
<ul>
    <li><strong>Toast Notifications:</strong> Success, warning, and error messages slide in unobtrusively using modern toast alerts.</li>
    <li><strong>Form Validation:</strong> Immediate visual feedback if you type an invalid price or forget a required field.</li>
</ul>
<p>A friction-free experience for your staff.</p>'''
            },
            {
                'feature_key': 'print_to_pdf',
                'title': 'Going Green: Print to PDF',
                'content': '''<h2>Digital Records Made Easy</h2>
<p>Want to save an invoice or ledger without wasting paper?</p>
<ul>
    <li><strong>PDF Export:</strong> Our print layouts are highly optimized. When you click print, you can select "Save as PDF" in your browser.</li>
    <li><strong>Perfect Formatting:</strong> Elements like navigation bars and buttons are automatically hidden on the printed/PDF document.</li>
</ul>
<p>Save money on thermal paper and keep digital archives.</p>'''
            },
            {
                'feature_key': 'continuous_updates',
                'title': 'Future Proof: Continuous Updates',
                'content': '''<h2>The Software That Grows With You</h2>
<p>The best part about a Cloud SaaS platform? You never have to install an update.</p>
<ul>
    <li><strong>Silent Upgrades:</strong> We regularly push performance improvements, security patches, and new features directly to the servers.</li>
    <li><strong>Instant Access:</strong> Next time you refresh the page, you instantly have the latest and greatest version of HendAxis PoS.</li>
</ul>
<p>Welcome to the future of retail management.</p>'''
            },
            {
                'feature_key': 'transfer_discrepancy_handling',
                'title': 'Feature Update: Smarter Transfer Receiving & Discrepancies',
                'content': '''<h2>Introducing Advanced Transfer Discrepancies!</h2>
<p>We've added a powerful new way to handle discrepancies when receiving inventory transfers.</p>
<ul>
    <li><strong>Partial Receipts:</strong> You can now accept some items while disputing others.</li>
    <li><strong>Shortages:</strong> If an item wasn't actually sent, selecting <em>Short Quantity</em> will automatically return the stock to the sender's inventory!</li>
    <li><strong>Damages/Expiry:</strong> If an item arrives damaged, the system will automatically create an <em>Awaiting Approval</em> Stock Adjustment for your auditor to review.</li>
</ul>
<p>This ensures your inventory ledgers remain perfectly accurate across all locations.</p>'''
            },
            {
                'feature_key': 'offline_mode',
                'title': 'Mastering Offline Mode: Keep Selling Without Internet',
                'content': '''<h2>Don't Let Bad Internet Stop Your Sales!</h2>
<p>Did you know HendAxis PoS is built with an Offline-First architecture? This means if your shop loses internet connection, you can keep selling without any interruptions.</p>
<ul>
    <li><strong>Automatic Syncing:</strong> Once your internet connection is restored, all offline sales will automatically sync to the server in the background.</li>
    <li><strong>Visual Indicators:</strong> Look for the cloud icon in the top navigation bar. A green checkmark means you're online, while an orange warning icon means you are operating offline.</li>
</ul>
<p>Keep your business running smoothly, no matter the connection!</p>'''
            },
            {
                'feature_key': 'digital_payments',
                'title': 'Streamline Your Checkout with Digital Payments',
                'content': '''<h2>Accept Mobile Money & Card Payments Seamlessly</h2>
<p>Tracking different payment methods is easier than ever with our integrated digital payment tracking.</p>
<ul>
    <li><strong>Multiple Payment Methods:</strong> You can split a single sale across Cash, Mobile Money, and E-Cash simultaneously.</li>
    <li><strong>Digital Confirmations:</strong> Shop Managers and Accountants can verify digital receipts directly from their dashboards, ensuring complete financial transparency.</li>
</ul>
<p>Reduce cash-handling errors and speed up your checkout process today!</p>'''
            },
            {
                'feature_key': 'inventory_auditing',
                'title': 'Feature Spotlight: The Auditor Workflow',
                'content': '''<h2>Keep Your Inventory Accurate with Stock Adjustments</h2>
<p>When physical stock doesn't match the system, our Auditor workflow ensures accountability.</p>
<ul>
    <li><strong>Request Adjustments:</strong> Shop Managers can log damages or shortages, which are flagged as 'Awaiting Approval'.</li>
    <li><strong>Auditor Verification:</strong> Users with the Auditor role must review and approve these discrepancies before the system inventory is permanently updated.</li>
    <li><strong>Full Ledger History:</strong> Every approved adjustment is logged in the inventory ledger with notes and timestamps.</li>
</ul>
<p>Start auditing your inventory regularly to prevent shrinkage and maintain accurate books!</p>'''
            },
            {
                'feature_key': 'tenant_management',
                'title': 'Manage Multiple Shops with Ease',
                'content': '''<h2>Scale Your Business Across Locations</h2>
<p>HendAxis PoS is designed to grow with your business. If you manage multiple branches or warehouses, you can control everything from a single Tenant Admin dashboard.</p>
<ul>
    <li><strong>Location-Specific Stock:</strong> Track inventory levels separately for each storefront or warehouse.</li>
    <li><strong>Stock Transfers:</strong> Easily move inventory between locations with our robust transfer system.</li>
    <li><strong>Role-Based Access:</strong> Assign specific users as 'Shop Managers' or 'Attendants' restricted to specific locations.</li>
</ul>
<p>Centralize your operations and get a bird's-eye view of your entire business!</p>'''
            }
        ]

        created_count = 0
        for msg in messages:
            obj, created = FeatureMessage.objects.get_or_create(
                feature_key=msg['feature_key'],
                defaults={
                    'title': msg['title'],
                    'content': msg['content'],
                    'is_active': True,
                    'created_by': admin_user
                }
            )
            if created:
                created_count += 1

        self.stdout.write(self.style.SUCCESS(f"Successfully seeded {created_count} NEW feature messages."))
        self.stdout.write(self.style.SUCCESS(f"Total messages now in DB: {FeatureMessage.objects.count()}"))
