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
                'feature_key': 'inventory_mastery',
                'title': 'Master Your Inventory: Batches & Reorders',
                'content': '''<h2>Never Run Out, Never Expire</h2>
<p>HendAxis PoS gives you complete control over your stock, down to the exact batch.</p>
<ul>
    <li><strong>Low Stock Alerts:</strong> Set custom reorder thresholds and get instant dashboard notifications before you run out.</li>
    <li><strong>Batch & Expiry Tracking:</strong> Selling perishables? The system enforces First-In-First-Out (FIFO) and warns you of expiring batches.</li>
    <li><strong>Price Change Center:</strong> Automatically track global price updates and ensure your shelf tags are always accurate.</li>
</ul>
<p>Optimize your inventory and eliminate shrinkage!</p>'''
            },
            {
                'feature_key': 'customer_tracking',
                'title': 'Build Relationships: Customer Tracking & Debt Management',
                'content': '''<h2>Know Your Customers Like Never Before</h2>
<p>Turn one-time buyers into loyal clients with our comprehensive Customer Management tools.</p>
<ul>
    <li><strong>Debt Tracking:</strong> Easily issue items on credit, track outstanding balances, and receive partial payments directly to their accounts.</li>
    <li><strong>Purchase History:</strong> View every past transaction a customer has made to understand their buying habits.</li>
    <li><strong>Account Balances:</strong> Customers can maintain positive balances (store credit) from overpayments or change from sales, which they can use for future purchases.</li>
</ul>
<p>Keep your customers coming back!</p>'''
            },
            {
                'feature_key': 'requesting_system',
                'title': 'Seamless Supply Chain: The Requesting System',
                'content': '''<h2>Move Stock Exactly Where It\\'s Needed</h2>
<p>Stop relying on phone calls and WhatsApp to request stock from the main warehouse.</p>
<ul>
    <li><strong>Digital Stock Requests:</strong> Shop managers can electronically request specific items and quantities from the central store.</li>
    <li><strong>Combine Requests:</strong> Stores Managers can instantly combine multiple pending requests from the same shop into a single, efficient transfer.</li>
    <li><strong>Discrepancy Handling:</strong> If a transfer arrives short or damaged, receiving shops can dispute individual items, triggering automatic auditor review.</li>
</ul>
<p>Keep your shelves stocked effortlessly.</p>'''
            },
            {
                'feature_key': 'expenditure_system',
                'title': 'Control Your Cash: The Expenditure System',
                'content': '''<h2>Track Every Penny Leaving the Till</h2>
<p>Cash management isn't just about sales; it's about tracking expenses.</p>
<ul>
    <li><strong>Direct Till Deductions:</strong> Shop Managers can log daily expenditures (like cleaning supplies or transport) directly out of the shop's cash on hand.</li>
    <li><strong>Accountant Approvals:</strong> Larger expense requests are automatically routed to the Accountant for review before cash can be dispersed.</li>
    <li><strong>Expense Categorization:</strong> Categorize your spending to see exactly where your operating budget is going each month.</li>
</ul>
<p>Total financial visibility for your retail operation.</p>'''
            },
            {
                'feature_key': 'workflow_settings',
                'title': 'Tailor the System to Your Business: Workflow Settings',
                'content': '''<h2>Enhance Your Process Effectively</h2>
<p>No two businesses are identical. HendAxis PoS offers powerful settings to enforce your standard operating procedures.</p>
<ul>
    <li><strong>Strict Sale Workflow:</strong> Enforce strict cashiering where attendants ring up items, but a dedicated Cashier must process the payment before dispatch.</li>
    <li><strong>Pricing Control Modes:</strong> Decide if Shop Managers can set their own local prices or if all pricing is locked by the central Accountant.</li>
    <li><strong>Hide Zero-Stock:</strong> Clean up the POS interface by automatically hiding products that are out of stock.</li>
</ul>
<p>Configure the platform to match your specific business rules.</p>'''
            },
            {
                'feature_key': 'multi_shop_umbrella',
                'title': 'Expand Your Empire: Multi-Shop Management',
                'content': '''<h2>One Organizational Umbrella</h2>
<p>Managing several shops has never been easier. Centralize your entire operation from a single dashboard.</p>
<ul>
    <li><strong>Location-Specific Visibility:</strong> Assign managers to specific locations. They see only what they need, while you see everything.</li>
    <li><strong>Cross-Shop Availability:</strong> Allow attendants to see stock levels at other branches so they can redirect customers if an item is sold out locally.</li>
    <li><strong>Real-Time Sync:</strong> Watch sales, inventory drops, and cash movements happen across all your locations simultaneously.</li>
</ul>
<p>Scale your business confidently.</p>'''
            },
            {
                'feature_key': 'auditor_ledger',
                'title': 'Trust But Verify: Auditing & The Inventory Ledger',
                'content': '''<h2>Ironclad Accountability</h2>
<p>When stock goes missing, you need answers immediately.</p>
<ul>
    <li><strong>The Auditor Workflow:</strong> Shop Managers can report damages, but they cannot permanently delete stock. A designated Auditor must review and approve all adjustments.</li>
    <li><strong>Immutable Ledger:</strong> Every sale, transfer, return, and adjustment is permanently recorded in the Inventory Ledger with a timestamp and the user's name.</li>
</ul>
<p>Protect your capital with enterprise-grade security.</p>'''
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
