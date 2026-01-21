"""
PDF receipt generation service for subscription payments.
Uses ReportLab to generate professional PDF receipts.
"""
import io
from decimal import Decimal
from django.conf import settings
from django.utils import timezone

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch, cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


class PDFReceiptService:
    """
    Service for generating PDF receipts for subscription payments.
    """
    
    @classmethod
    def is_available(cls):
        """Check if PDF generation is available."""
        return REPORTLAB_AVAILABLE
    
    @classmethod
    def generate_receipt(cls, payment):
        """
        Generate a PDF receipt for a subscription payment.
        
        Args:
            payment: SubscriptionPayment instance
        
        Returns:
            bytes: PDF file content
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError("ReportLab is required for PDF generation. Install with: pip install reportlab")
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=1.5*cm,
            leftMargin=1.5*cm,
            topMargin=1.5*cm,
            bottomMargin=1.5*cm
        )
        
        styles = getSampleStyleSheet()
        story = []
        
        # Custom styles
        title_style = ParagraphStyle(
            'Title',
            parent=styles['Heading1'],
            fontSize=24,
            alignment=TA_CENTER,
            spaceAfter=20,
            textColor=colors.HexColor('#2c3e50')
        )
        
        header_style = ParagraphStyle(
            'Header',
            parent=styles['Normal'],
            fontSize=12,
            alignment=TA_CENTER,
            spaceAfter=10,
            textColor=colors.HexColor('#7f8c8d')
        )
        
        label_style = ParagraphStyle(
            'Label',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#7f8c8d')
        )
        
        value_style = ParagraphStyle(
            'Value',
            parent=styles['Normal'],
            fontSize=12,
            textColor=colors.HexColor('#2c3e50')
        )
        
        # Header
        story.append(Paragraph("SUBSCRIPTION RECEIPT", title_style))
        story.append(Paragraph("POS System - Subscription Management", header_style))
        story.append(Spacer(1, 20))
        
        # Receipt number and date
        receipt_data = [
            ['Receipt Number:', payment.receipt_number],
            ['Date:', payment.created_at.strftime('%B %d, %Y')],
            ['Status:', payment.get_status_display()],
        ]
        
        receipt_table = Table(receipt_data, colWidths=[2.5*inch, 4*inch])
        receipt_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#7f8c8d')),
            ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#2c3e50')),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(receipt_table)
        story.append(Spacer(1, 30))
        
        # Tenant/Customer Information
        story.append(Paragraph("BILLED TO", ParagraphStyle(
            'SectionHeader',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#27ae60'),
            spaceAfter=10
        )))
        
        tenant = payment.tenant
        customer_data = [
            ['Organization:', tenant.name],
            ['Email:', tenant.email],
            ['Phone:', tenant.phone],
        ]
        if tenant.address:
            customer_data.append(['Address:', tenant.address])
        
        customer_table = Table(customer_data, colWidths=[2*inch, 4.5*inch])
        customer_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#7f8c8d')),
            ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#2c3e50')),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(customer_table)
        story.append(Spacer(1, 30))
        
        # Payment Details
        story.append(Paragraph("PAYMENT DETAILS", ParagraphStyle(
            'SectionHeader',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#27ae60'),
            spaceAfter=10
        )))
        
        # Determine currency symbol
        currency_symbols = {
            'GHS': 'GH₵',
            'USD': '$',
            'NGN': '₦',
            'EUR': '€',
            'GBP': '£',
        }
        currency_symbol = currency_symbols.get(payment.currency, payment.currency)
        
        payment_details_data = [
            ['Description', 'Period', 'Amount'],
        ]
        
        if payment.payment_type == 'ONBOARDING':
            description = 'Onboarding Fee'
            period = 'One-time'
        elif payment.payment_type in ['SUBSCRIPTION', 'RENEWAL']:
            description = f'{payment.plan_name} Subscription' if payment.plan_name else 'Subscription'
            if payment.period_start and payment.period_end:
                period = f"{payment.period_start.strftime('%b %d, %Y')} - {payment.period_end.strftime('%b %d, %Y')}"
            else:
                period = 'Monthly'
        else:
            description = payment.get_payment_type_display()
            period = '-'
        
        payment_details_data.append([description, period, f"{currency_symbol}{payment.amount:,.2f}"])
        
        # Add plan details if available
        if payment.plan_details:
            if payment.plan_details.get('shop_count'):
                payment_details_data.append([
                    f"  Shops included: {payment.plan_details.get('shop_count')}", '', ''
                ])
        
        payment_table = Table(payment_details_data, colWidths=[3*inch, 2*inch, 1.5*inch])
        payment_table.setStyle(TableStyle([
            # Header row
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#27ae60')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            # Data rows
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#2c3e50')),
            ('ALIGN', (2, 1), (2, -1), 'RIGHT'),
            # Grid
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#27ae60')),
            ('LINEBELOW', (0, -1), (-1, -1), 1, colors.HexColor('#bdc3c7')),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
        ]))
        story.append(payment_table)
        story.append(Spacer(1, 20))
        
        # Total
        total_data = [
            ['Total Paid:', f"{currency_symbol}{payment.amount:,.2f}"],
        ]
        total_table = Table(total_data, colWidths=[5*inch, 1.5*inch])
        total_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 14),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#27ae60')),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#e8f8f5')),
            ('PADDING', (0, 0), (-1, -1), 15),
        ]))
        story.append(total_table)
        story.append(Spacer(1, 30))
        
        # Payment method
        if payment.payment_method:
            method_data = [
                ['Payment Method:', payment.get_payment_method_display()],
            ]
            if payment.paystack_reference:
                method_data.append(['Reference:', payment.paystack_reference])
            if payment.transaction_reference:
                method_data.append(['Transaction ID:', payment.transaction_reference])
            
            method_table = Table(method_data, colWidths=[2.5*inch, 4*inch])
            method_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#7f8c8d')),
                ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#2c3e50')),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(method_table)
            story.append(Spacer(1, 20))
        
        # Footer
        story.append(Spacer(1, 40))
        footer_style = ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontSize=9,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#95a5a6')
        )
        story.append(Paragraph(
            f"Generated on {timezone.now().strftime('%B %d, %Y at %H:%M')}",
            footer_style
        ))
        story.append(Paragraph(
            "Thank you for your subscription!",
            ParagraphStyle('ThankYou', parent=footer_style, fontSize=11, textColor=colors.HexColor('#27ae60'))
        ))
        
        # Build PDF
        doc.build(story)
        
        buffer.seek(0)
        return buffer.getvalue()
    
    @classmethod
    def get_receipt_filename(cls, payment):
        """Generate a filename for the receipt."""
        return f"Receipt_{payment.receipt_number}_{payment.tenant.slug}.pdf"
