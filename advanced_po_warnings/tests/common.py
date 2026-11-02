# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase, new_test_user

# All test orders live in 2001, far away from demo data, so the period-based rules
# only ever see the records created by the tests.
BASE_DATE = datetime(2001, 6, 15, 10, 0, 0)

RULES_OFF = {
    'po_warning_enabled': True,
    'po_warning_user_ids': [(5, 0, 0)],
    'po_dup_active': False,
    'po_freq_active': False,
    'po_dest_active': False,
}


class POWarningCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Assertions use English strings whatever the database's default language is
        # (a database created in Arabic does not even have English active).
        cls.env['res.lang']._activate_lang('en_US')
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, lang='en_US'))
        cls.company = cls.env.company
        cls.company.write(dict(RULES_OFF, po_warning_enabled=False))
        cls.supervisor_group = cls.env.ref('advanced_po_warnings.group_po_warning_supervisor')

        cls.purchase_user = new_test_user(
            cls.env, login='po_warn_buyer', name='Buyer', lang='en_US',
            groups='base.group_user,purchase.group_purchase_user,stock.group_stock_user')
        cls.supervisor = new_test_user(
            cls.env, login='po_warn_supervisor', name='Supervisor One', lang='en_US',
            groups='base.group_user,advanced_po_warnings.group_po_warning_supervisor')
        cls.supervisor_2 = new_test_user(
            cls.env, login='po_warn_supervisor_2', name='Supervisor Two', lang='en_US',
            groups='base.group_user,advanced_po_warnings.group_po_warning_supervisor')

        Partner = cls.env['res.partner']
        cls.vendor_a = Partner.create({'name': 'Vendor A', 'is_company': True})
        cls.vendor_a_contact = Partner.create({'name': 'Vendor A - Buyer', 'parent_id': cls.vendor_a.id})
        cls.vendor_b = Partner.create({'name': 'Vendor B', 'is_company': True})

        Product = cls.env['product.product']
        cls.p1 = Product.create({'name': 'Warn Item 1', 'type': 'consu', 'is_storable': True})
        cls.p2 = Product.create({'name': 'Warn Item 2', 'type': 'consu', 'is_storable': True})
        cls.p3 = Product.create({'name': 'Warn Item 3', 'type': 'consu', 'is_storable': True})

        cls.wh1 = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.wh2 = cls.env['stock.warehouse'].create({'name': 'Warn WH 2', 'code': 'WWH2'})
        cls.wh3 = cls.env['stock.warehouse'].create({'name': 'Warn WH 3', 'code': 'WWH3'})
        cls.in_wh1 = cls.wh1.in_type_id
        cls.in_wh2 = cls.wh2.in_type_id
        cls.in_wh3 = cls.wh3.in_type_id

    # ---------------------------------------------------------------- helpers
    def configure(self, **vals):
        values = dict(RULES_OFF)
        values.update(vals)
        self.company.write(values)

    def make_po(self, partner, lines, days=0, picking_type=None, user=None, confirm=False):
        """lines: list of (product, qty) or (product, qty, uom)."""
        order_lines = []
        for line in lines:
            vals = {'product_id': line[0].id, 'product_qty': line[1]}
            if len(line) > 2:
                vals['product_uom'] = line[2].id
            order_lines.append((0, 0, vals))
        Order = self.env['purchase.order'].with_user(user) if user else self.env['purchase.order']
        order = Order.create({
            'partner_id': partner.id,
            'date_order': BASE_DATE + timedelta(days=days),
            'picking_type_id': (picking_type or self.in_wh1).id,
            'order_line': order_lines,
        })
        if confirm:
            order.button_confirm()
            self.assertEqual(order.state, 'purchase')
        return order.sudo()

    def hits(self, order, rule=None):
        results = order._po_warning_evaluate()[0]
        return [h for h in results if not rule or h['rule'] == rule]
