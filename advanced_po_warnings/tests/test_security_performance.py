# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tools.sql import index_exists

from .common import BASE_DATE, POWarningCommon


@tagged('post_install', '-at_install', 'po_warnings')
class TestPOWarningSecurity(POWarningCommon):

    def setUp(self):
        super().setUp()
        self.configure(po_dup_active=True, po_dup_vendor_scope='same', po_dup_days=10,
                       po_dup_match_qty=True, po_dup_mode='block')

    def test_review_fields_cannot_be_forged(self):
        self.make_po(self.vendor_a, [(self.p1, 5)], confirm=True)
        order = self.make_po(self.vendor_a, [(self.p1, 5)], user=self.purchase_user)
        order.with_user(self.purchase_user).button_confirm()
        as_user = order.with_user(self.purchase_user)
        for vals in ({'po_review_state': 'bypassed'}, {'po_review_state': 'none'},
                     {'po_review_note': 'self approved'}, {'po_reviewed_by_id': self.supervisor.id}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                as_user.write(vals)
        # even supervisors must go through the review workflow
        with self.assertRaises(AccessError):
            order.with_user(self.supervisor).write({'po_review_state': 'bypassed'})
        self.assertEqual(order.po_review_state, 'blocked')

    def test_review_fields_on_create(self):
        Order = self.env['purchase.order'].with_user(self.purchase_user)
        vals = {'partner_id': self.vendor_a.id, 'date_order': BASE_DATE}
        self.assertEqual(Order.create(dict(vals, po_review_state='none')).po_review_state, 'none')
        with self.assertRaises(AccessError):
            Order.create(dict(vals, po_review_state='bypassed'))

    def test_copy_resets_review(self):
        self.make_po(self.vendor_a, [(self.p1, 5)], confirm=True)
        order = self.make_po(self.vendor_a, [(self.p1, 5)])
        order.button_confirm()
        copy = order.with_user(self.purchase_user).copy()
        self.assertEqual(copy.po_review_state, 'none')
        self.assertFalse(copy.po_warning_ids)

    def test_warning_records_are_read_only_for_buyers(self):
        self.make_po(self.vendor_a, [(self.p1, 5)], confirm=True)
        order = self.make_po(self.vendor_a, [(self.p1, 5)])
        order.button_confirm()
        warning = order.po_warning_ids.with_user(self.purchase_user)
        self.assertTrue(warning.message)
        with self.assertRaises(AccessError):
            warning.write({'message': 'edited'})
        with self.assertRaises(AccessError):
            warning.unlink()
        with self.assertRaises(AccessError):
            self.env['purchase.order.warning'].with_user(self.purchase_user).create({
                'order_id': order.id, 'rule': 'duplicate', 'mode': 'warning', 'message': 'x'})

    def test_multi_company_isolation(self):
        company_b = self.env['res.company'].create({'name': 'Warn Co B'})
        company_b.write({'po_warning_enabled': True, 'po_dup_active': True,
                         'po_dup_vendor_scope': 'same', 'po_dup_days': 10, 'po_dup_mode': 'block'})
        wh_b = self.env['stock.warehouse'].search([('company_id', '=', company_b.id)], limit=1)
        self.make_po(self.vendor_a, [(self.p1, 5)], confirm=True)  # company A
        order_b = self.env['purchase.order'].with_company(company_b).create({
            'partner_id': self.vendor_a.id, 'date_order': BASE_DATE, 'company_id': company_b.id,
            'picking_type_id': wh_b.in_type_id.id,
            'order_line': [(0, 0, {'product_id': self.p1.id, 'product_qty': 5})],
        })
        self.assertFalse(self.hits(order_b))
        order_b.button_confirm()
        self.assertEqual(order_b.state, 'purchase')

    def test_supervisor_group_implies_purchase_user(self):
        self.assertTrue(self.supervisor.has_group('purchase.group_purchase_user'))
        self.assertIn(self.env.ref('base.user_admin'), self.supervisor_group.users)


@tagged('post_install', '-at_install', 'po_warnings')
class TestPOWarningPerformance(POWarningCommon):

    def setUp(self):
        super().setUp()
        self.configure(
            po_dup_active=True, po_dup_vendor_scope='any', po_dup_days=30, po_dup_mode='warning',
            po_freq_active=True, po_freq_vendor_scope='any', po_freq_doc_scope='all', po_freq_days=30,
            po_freq_min_orders=1, po_freq_mode='warning',
            po_dest_active=True, po_dest_target='warehouse', po_dest_warehouse_ids=[(5, 0, 0)],
            po_dest_vendor_scope='any', po_dest_doc_scope='all', po_dest_days=30, po_dest_mode='warning')
        products = self.p1 | self.p2 | self.p3
        vendors = [self.vendor_a, self.vendor_b]
        self.orders = self.env['purchase.order']
        for i in range(24):
            lines = [(products[i % 3], 1 + i % 4), (products[(i + 1) % 3], 2)]
            self.orders |= self.make_po(vendors[i % 2], lines, days=i % 20)

    def _count_queries(self, orders):
        self.env.invalidate_all()
        start = self.env.cr.sql_log_count
        results = orders._po_warning_evaluate()
        return self.env.cr.sql_log_count - start, results

    def test_query_count_is_independent_of_batch_size(self):
        # Warm the registry-level caches (translated selection labels) once.
        self.orders[:1]._po_warning_evaluate()
        small, small_res = self._count_queries(self.orders[:2])
        large, large_res = self._count_queries(self.orders)
        self.assertTrue(all(small_res) and all(large_res), 'every order is flagged by every rule')
        self.assertEqual(small, large, 'evaluating 24 orders costs the same queries as 2 (no N+1)')
        self.assertLessEqual(large, 15)

    def test_batch_results_match_single_evaluation(self):
        batch = self.orders._po_warning_evaluate()
        for order, hits in zip(self.orders[:6], batch[:6]):
            single = order._po_warning_evaluate()[0]
            key = lambda h: (h['rule'], h['product_id'] or 0)
            self.assertEqual(sorted(map(key, hits)), sorted(map(key, single)))
            self.assertEqual(sorted(h['conflict_ids'] for h in hits),
                             sorted(h['conflict_ids'] for h in single))

    def test_period_index_exists(self):
        self.assertTrue(index_exists(self.env.cr, 'purchase_order_po_warning_company_date_idx'))
