# -*- coding: utf-8 -*-
from odoo.tests import tagged

from .common import POWarningCommon


@tagged('post_install', '-at_install', 'po_warnings')
class TestPOWarningFrequency(POWarningCommon):

    def setUp(self):
        super().setUp()
        self.configure(po_freq_active=True, po_freq_vendor_scope='any', po_freq_doc_scope='confirmed',
                       po_freq_days=30, po_freq_min_orders=1, po_freq_mode='warning')

    def test_document_scope(self):
        rfq = self.make_po(self.vendor_a, [(self.p1, 1)], days=-5)
        confirmed = self.make_po(self.vendor_a, [(self.p1, 3)], days=-10, confirm=True)
        order = self.make_po(self.vendor_a, [(self.p1, 2)])

        hits = self.hits(order, 'frequency')
        self.assertEqual(hits[0]['conflict_ids'], confirmed.ids, 'RFQs are ignored for confirmed scope')

        self.company.po_freq_doc_scope = 'all'
        hits = self.hits(order, 'frequency')
        self.assertEqual(sorted(hits[0]['conflict_ids']), sorted((rfq | confirmed).ids))

    def test_to_approve_orders_count_as_confirmed(self):
        self.company.write({'po_double_validation': 'two_step', 'po_double_validation_amount': 0})
        pending = self.make_po(self.vendor_a, [(self.p1, 1)], days=-1, user=self.purchase_user)
        pending.with_user(self.purchase_user).button_confirm()
        self.assertEqual(pending.state, 'to approve')
        order = self.make_po(self.vendor_a, [(self.p1, 1)])
        self.assertEqual(self.hits(order, 'frequency')[0]['conflict_ids'], pending.ids)

    def test_threshold(self):
        self.company.po_freq_min_orders = 2
        first = self.make_po(self.vendor_a, [(self.p1, 1)], days=-3, confirm=True)
        order = self.make_po(self.vendor_a, [(self.p1, 1)])
        self.assertFalse(self.hits(order, 'frequency'))
        second = self.make_po(self.vendor_b, [(self.p1, 1)], days=-2, confirm=True)
        hits = self.hits(order, 'frequency')
        self.assertEqual(len(hits), 1)
        self.assertEqual(sorted(hits[0]['conflict_ids']), sorted((first | second).ids))
        self.assertIn('2 time(s)', hits[0]['message'])

    def test_vendor_scope_permutations(self):
        self.make_po(self.vendor_a, [(self.p1, 1)], days=-1, confirm=True)
        for scope, vendor, expected in [
            ('same', self.vendor_a_contact, True), ('same', self.vendor_b, False),
            ('different', self.vendor_a, False), ('different', self.vendor_b, True),
            ('any', self.vendor_b, True),
        ]:
            with self.subTest(scope=scope, vendor=vendor.name):
                self.company.po_freq_vendor_scope = scope
                order = self.make_po(vendor, [(self.p1, 1)])
                self.assertEqual(bool(self.hits(order, 'frequency')), expected)

    def test_time_window(self):
        self.make_po(self.vendor_a, [(self.p1, 1)], days=-31, confirm=True)
        order = self.make_po(self.vendor_a, [(self.p1, 1)])
        self.assertFalse(self.hits(order, 'frequency'))
        self.company.po_freq_days = 31
        self.assertTrue(self.hits(order, 'frequency'))

    def test_one_hit_per_reordered_product(self):
        self.make_po(self.vendor_a, [(self.p1, 1), (self.p3, 1)], days=-1, confirm=True)
        order = self.make_po(self.vendor_b, [(self.p1, 4), (self.p2, 4), (self.p3, 4)])
        hits = self.hits(order, 'frequency')
        self.assertEqual(sorted(h['product_id'] for h in hits), sorted((self.p1 | self.p3).ids))
        self.assertTrue(all(h['mode'] == 'warning' for h in hits))

    def test_block_mode(self):
        self.company.po_freq_mode = 'block'
        self.make_po(self.vendor_a, [(self.p1, 1)], days=-1, confirm=True)
        order = self.make_po(self.vendor_b, [(self.p1, 9)], user=self.purchase_user)
        order.with_user(self.purchase_user).button_confirm()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.po_review_state, 'blocked')
        self.assertEqual(order.po_warning_ids.product_id, self.p1)
