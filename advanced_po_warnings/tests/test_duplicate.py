# -*- coding: utf-8 -*-
from odoo.tests import tagged

from .common import POWarningCommon


@tagged('post_install', '-at_install', 'po_warnings')
class TestPOWarningDuplicate(POWarningCommon):

    def setUp(self):
        super().setUp()
        self.configure(po_dup_active=True, po_dup_vendor_scope='same', po_dup_days=10,
                       po_dup_match_qty=True, po_dup_mode='warning')

    def test_vendor_scope_permutations(self):
        cases = [
            # scope, vendor of the new order, expected hit
            ('same', self.vendor_a, True),
            ('same', self.vendor_b, False),
            ('different', self.vendor_a, False),
            ('different', self.vendor_b, True),
            ('any', self.vendor_a, True),
            ('any', self.vendor_b, True),
        ]
        existing = self.make_po(self.vendor_a, [(self.p1, 5), (self.p2, 2)])
        for scope, vendor, expected in cases:
            with self.subTest(scope=scope, vendor=vendor.name):
                self.company.po_dup_vendor_scope = scope
                order = self.make_po(vendor, [(self.p2, 2), (self.p1, 5)])
                hits = self.hits(order, 'duplicate')
                self.assertEqual(bool(hits), expected)
                if expected:
                    self.assertIn(existing.id, hits[0]['conflict_ids'])
                order.button_cancel()  # keep the next case independent

    def test_vendor_contact_counts_as_same_vendor(self):
        existing = self.make_po(self.vendor_a, [(self.p1, 5)])
        order = self.make_po(self.vendor_a_contact, [(self.p1, 5)])
        self.assertEqual(self.hits(order, 'duplicate')[0]['conflict_ids'], existing.ids)

    def test_period_boundaries(self):
        before_in = self.make_po(self.vendor_a, [(self.p1, 5)], days=-9)
        self.make_po(self.vendor_a, [(self.p1, 5)], days=-11)
        after_in = self.make_po(self.vendor_a, [(self.p1, 5)], days=9)
        self.make_po(self.vendor_a, [(self.p1, 5)], days=11)
        order = self.make_po(self.vendor_a, [(self.p1, 5)])
        self.assertEqual(sorted(self.hits(order, 'duplicate')[0]['conflict_ids']),
                         sorted((before_in | after_in).ids))

    def test_match_quantities(self):
        self.make_po(self.vendor_a, [(self.p1, 5)])
        order = self.make_po(self.vendor_a, [(self.p1, 6)])
        self.assertFalse(self.hits(order, 'duplicate'))
        self.company.po_dup_match_qty = False
        self.assertTrue(self.hits(order, 'duplicate'))

    def test_product_set_must_be_identical(self):
        self.make_po(self.vendor_a, [(self.p1, 5)])
        self.make_po(self.vendor_a, [(self.p1, 5), (self.p2, 1), (self.p3, 1)])
        order = self.make_po(self.vendor_a, [(self.p1, 5), (self.p2, 1)])
        self.assertFalse(self.hits(order, 'duplicate'))

    def test_split_lines_and_uom_are_normalised(self):
        dozen = self.env.ref('uom.product_uom_dozen')
        existing = self.make_po(self.vendor_a, [(self.p1, 12)])
        # 2 lines (4 + 8 units) and 1 dozen are both 12 units of p1.
        split = self.make_po(self.vendor_a, [(self.p1, 4), (self.p1, 8)])
        in_dozen = self.make_po(self.vendor_a, [(self.p1, 1, dozen)])
        self.assertIn(existing.id, self.hits(split, 'duplicate')[0]['conflict_ids'])
        self.assertIn(existing.id, self.hits(in_dozen, 'duplicate')[0]['conflict_ids'])

    def test_fractional_quantities(self):
        existing = self.make_po(self.vendor_a, [(self.p1, 0.1 + 0.2)])
        order = self.make_po(self.vendor_a, [(self.p1, 0.3)])
        self.assertEqual(self.hits(order, 'duplicate')[0]['conflict_ids'], existing.ids)

    def test_cancelled_orders_are_ignored_and_rfqs_count(self):
        cancelled = self.make_po(self.vendor_a, [(self.p1, 5)])
        cancelled.button_cancel()
        rfq = self.make_po(self.vendor_a, [(self.p1, 5)])
        order = self.make_po(self.vendor_a, [(self.p1, 5)])
        self.assertEqual(self.hits(order, 'duplicate')[0]['conflict_ids'], rfq.ids)

    def test_order_without_products_is_skipped(self):
        self.make_po(self.vendor_a, [])
        order = self.make_po(self.vendor_a, [])
        self.assertFalse(self.hits(order))

    def test_two_duplicates_confirmed_together_flag_each_other(self):
        o1 = self.make_po(self.vendor_a, [(self.p1, 5)])
        o2 = self.make_po(self.vendor_a, [(self.p1, 5)])
        r1, r2 = (o1 | o2)._po_warning_evaluate()
        self.assertEqual(r1[0]['conflict_ids'], o2.ids)
        self.assertEqual(r2[0]['conflict_ids'], o1.ids)

    def test_warning_mode_confirms_and_logs(self):
        self.make_po(self.vendor_a, [(self.p1, 5)], confirm=True)
        order = self.make_po(self.vendor_a, [(self.p1, 5)], user=self.purchase_user)
        action = order.with_user(self.purchase_user).button_confirm()
        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.po_review_state, 'none')
        self.assertEqual(action['params']['type'], 'warning')
        self.assertEqual(order.po_warning_ids.mapped('rule'), ['duplicate'])
        self.assertEqual(order.po_warning_ids.mode, 'warning')
        self.assertIn('Confirmed with warnings', order.message_ids[0].body)
        self.assertFalse(order.activity_ids)

    def test_block_mode_halts_confirmation(self):
        self.company.po_dup_mode = 'block'
        self.make_po(self.vendor_a, [(self.p1, 5)], confirm=True)
        order = self.make_po(self.vendor_a, [(self.p1, 5)], user=self.purchase_user)
        action = order.with_user(self.purchase_user).button_confirm()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.po_review_state, 'blocked')
        self.assertEqual(action['params']['type'], 'danger')
        self.assertEqual(order.po_warning_ids.mode, 'block')
        self.assertEqual(order.po_warning_count, 1)
        self.assertIn('Order confirmation blocked', order.message_ids[0].body)
