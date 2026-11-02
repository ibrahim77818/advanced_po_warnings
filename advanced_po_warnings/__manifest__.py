# -*- coding: utf-8 -*-
{
    'name': 'Advanced Purchase Order Warnings',
    'summary': 'Duplicate order, reorder frequency and destination warnings on purchase '
               'orders, with optional blocking, supervisor review, bypass and rejection.',
    'description': """
Advanced Purchase Order Warnings
================================
Configurable control checks that run when a purchase order is confirmed:

* **Order duplicate** - same products (optionally same quantities) ordered from the
  same / a different / any vendor within N days.
* **Item reorder frequency** - a product already ordered N or more times within a
  time window, on confirmed orders only or on RFQs and orders.
* **Destination** - orders delivered to monitored locations or warehouses within a
  time window (or always, for restricted destinations).

Each check is either *Warning only* or *Warning + block confirmation*. Blocked orders
get a detailed alert, a chatter message and a review activity for the designated
supervisors, who can bypass (and confirm) or reject the order with a justification.
""",
    'version': '19.0.1.0.0',
    'category': 'Inventory/Purchase',
    'author': 'Ibrahim Yahia',
    'website': 'https://www.linkedin.com/in/ibrahim-yahia',
    'license': 'LGPL-3',
    'depends': ['purchase_stock'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/mail_activity_type_data.xml',
        'views/po_warning_templates.xml',
        'views/res_config_settings_views.xml',
        'views/purchase_order_warning_views.xml',
        'wizard/po_warning_review_wizard_views.xml',
        'views/purchase_order_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'advanced_po_warnings/static/src/scss/po_warnings.scss',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
