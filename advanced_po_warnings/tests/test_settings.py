# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import POWarningCommon


@tagged('post_install', '-at_install', 'po_warnings')
class TestPOWarningSettings(POWarningCommon):

    def test_disabled_by_default(self):
        company = self.env['res.company'].create({'name': 'Fresh Co'})
        self.assertFalse(company.po_warning_enabled)
        settings = self.env['res.config.settings'].with_company(company).create({})
        self.assertFalse(settings.po_warning_enabled)

    def test_master_switch_off_ignores_all_rules(self):
        self.configure(po_warning_enabled=False, po_dup_active=True, po_dup_mode='block',
                       po_freq_active=True, po_freq_mode='block', po_dest_active=True,
                       po_dest_days=0, po_dest_mode='block')
        self.make_po(self.vendor_a, [(self.p1, 5)], confirm=True)
        order = self.make_po(self.vendor_a, [(self.p1, 5)])
        self.assertTrue(order.button_confirm())
        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.po_review_state, 'none')
        self.assertFalse(order.po_warning_ids)
        self.assertEqual(order.po_warning_preview_level, 'none')

    def test_enabled_without_active_rules_is_transparent(self):
        self.configure()
        self.make_po(self.vendor_a, [(self.p1, 5)], confirm=True)
        order = self.make_po(self.vendor_a, [(self.p1, 5)])
        self.assertTrue(order.button_confirm())
        self.assertEqual(order.state, 'purchase')
        self.assertFalse(order.po_warning_ids)

    def test_config_settings_roundtrip(self):
        settings = self.env['res.config.settings'].create({
            'po_warning_enabled': True,
            'po_warning_user_ids': [(6, 0, self.supervisor.ids)],
            'po_dup_active': True, 'po_dup_vendor_scope': 'different', 'po_dup_days': 12,
            'po_dup_match_qty': False, 'po_dup_mode': 'block',
            'po_freq_active': True, 'po_freq_vendor_scope': 'same', 'po_freq_doc_scope': 'all',
            'po_freq_days': 45, 'po_freq_min_orders': 3, 'po_freq_mode': 'block',
            'po_dest_active': True, 'po_dest_target': 'location',
            'po_dest_location_ids': [(6, 0, self.wh2.lot_stock_id.ids)],
            'po_dest_vendor_scope': 'different', 'po_dest_doc_scope': 'confirmed',
            'po_dest_days': 0, 'po_dest_mode': 'block',
        })
        settings.execute()
        c = self.company
        self.assertTrue(c.po_warning_enabled)
        self.assertEqual(c.po_warning_user_ids, self.supervisor)
        self.assertEqual((c.po_dup_vendor_scope, c.po_dup_days, c.po_dup_match_qty, c.po_dup_mode),
                         ('different', 12, False, 'block'))
        self.assertEqual((c.po_freq_vendor_scope, c.po_freq_doc_scope, c.po_freq_days,
                          c.po_freq_min_orders, c.po_freq_mode), ('same', 'all', 45, 3, 'block'))
        self.assertEqual((c.po_dest_target, c.po_dest_location_ids, c.po_dest_vendor_scope,
                          c.po_dest_doc_scope, c.po_dest_days, c.po_dest_mode),
                         ('location', self.wh2.lot_stock_id, 'different', 'confirmed', 0, 'block'))

    def test_value_constraints(self):
        for field, value in [('po_dup_days', 0), ('po_freq_days', 0),
                             ('po_freq_min_orders', 0), ('po_dest_days', -1)]:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    self.company.write({field: value})

    def test_review_filters_on_rfq_and_po_search_views(self):
        for xmlid in ('purchase.view_purchase_order_filter', 'purchase.purchase_order_view_search'):
            with self.subTest(view=xmlid):
                arch = self.env['purchase.order'].get_view(self.env.ref(xmlid).id, 'search')['arch']
                for name in ('po_warning_blocked', 'po_warning_bypassed', 'po_warning_rejected',
                             'groupby_po_review_state'):
                    self.assertIn('name="%s"' % name, arch)

    def test_blocked_orders_action_uses_plain_list(self):
        action = self.env.ref('advanced_po_warnings.action_po_warning_blocked_orders')
        self.assertEqual(action.view_id, self.env.ref('purchase.purchase_order_view_tree'))
        arch = self.env['purchase.order'].get_view(action.view_id.id, 'tree')['arch']
        self.assertIn('po_review_state', arch)
