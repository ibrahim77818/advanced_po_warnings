# -*- coding: utf-8 -*-
from odoo.tests import tagged

from .common import POWarningCommon


@tagged('post_install', '-at_install', 'po_warnings')
class TestPOWarningDestination(POWarningCommon):

    def setUp(self):
        super().setUp()
        self.configure(po_dest_active=True, po_dest_target='warehouse',
                       po_dest_warehouse_ids=[(6, 0, self.wh2.ids)], po_dest_location_ids=[(5, 0, 0)],
                       po_dest_vendor_scope='any', po_dest_doc_scope='all', po_dest_days=7,
                       po_dest_mode='warning')

    def test_monitored_warehouse(self):
        existing = self.make_po(self.vendor_a, [(self.p1, 1)], days=-3, picking_type=self.in_wh2)
        self.make_po(self.vendor_a, [(self.p1, 1)], days=-3, picking_type=self.in_wh3)
        order = self.make_po(self.vendor_b, [(self.p2, 1)], picking_type=self.in_wh2)
        hits = self.hits(order, 'destination')
        self.assertEqual(hits[0]['conflict_ids'], existing.ids)
        self.assertEqual(hits[0]['warehouse_id'], self.wh2.id)

        unmonitored = self.make_po(self.vendor_b, [(self.p2, 1)], picking_type=self.in_wh3)
        self.assertFalse(self.hits(unmonitored, 'destination'))

    def test_empty_warehouse_list_monitors_all(self):
        self.company.po_dest_warehouse_ids = [(5, 0, 0)]
        existing = self.make_po(self.vendor_a, [(self.p1, 1)], days=-1, picking_type=self.in_wh3)
        order = self.make_po(self.vendor_b, [(self.p2, 1)], picking_type=self.in_wh3)
        self.assertEqual(self.hits(order, 'destination')[0]['conflict_ids'], existing.ids)

    def test_time_window(self):
        self.make_po(self.vendor_a, [(self.p1, 1)], days=-8, picking_type=self.in_wh2)
        order = self.make_po(self.vendor_b, [(self.p2, 1)], picking_type=self.in_wh2)
        self.assertFalse(self.hits(order, 'destination'))

    def test_restricted_destination_without_window(self):
        self.company.po_dest_days = 0
        order = self.make_po(self.vendor_b, [(self.p2, 1)], picking_type=self.in_wh2)
        hits = self.hits(order, 'destination')
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['conflict_ids'], [])
        self.assertIn('restricted', hits[0]['message'])

    def test_location_target_includes_sublocations(self):
        sub_location = self.env['stock.location'].create({
            'name': 'Cold Room', 'location_id': self.wh2.lot_stock_id.id, 'usage': 'internal'})
        picking_type = self.in_wh2.copy({'name': 'Receipts Cold Room', 'sequence_code': 'COLD',
                                         'default_location_dest_id': sub_location.id})
        self.company.write({'po_dest_target': 'location',
                            'po_dest_location_ids': [(6, 0, self.wh2.lot_stock_id.ids)]})
        existing = self.make_po(self.vendor_a, [(self.p1, 1)], days=2, picking_type=picking_type)
        order = self.make_po(self.vendor_b, [(self.p2, 1)], picking_type=picking_type)
        hits = self.hits(order, 'destination')
        self.assertEqual(hits[0]['conflict_ids'], existing.ids)
        self.assertEqual(hits[0]['location_id'], sub_location.id)

        # Same warehouse but the parent stock location is a different destination.
        other = self.make_po(self.vendor_b, [(self.p2, 1)], picking_type=self.in_wh2)
        self.assertFalse(self.hits(other, 'destination'))

        self.company.po_dest_location_ids = self.wh3.lot_stock_id
        self.assertFalse(self.hits(order, 'destination'), 'location outside monitored tree')

    def test_document_and_vendor_scope(self):
        rfq = self.make_po(self.vendor_a, [(self.p1, 1)], days=-1, picking_type=self.in_wh2)
        order = self.make_po(self.vendor_b, [(self.p2, 1)], picking_type=self.in_wh2)
        self.company.po_dest_doc_scope = 'confirmed'
        self.assertFalse(self.hits(order, 'destination'))
        rfq.button_confirm()
        self.assertTrue(self.hits(order, 'destination'))
        self.company.po_dest_vendor_scope = 'same'
        self.assertFalse(self.hits(order, 'destination'))
        self.company.po_dest_vendor_scope = 'different'
        self.assertTrue(self.hits(order, 'destination'))

    def test_block_mode(self):
        self.company.write({'po_dest_mode': 'block', 'po_dest_days': 0})
        order = self.make_po(self.vendor_b, [(self.p2, 1)], picking_type=self.in_wh2, user=self.purchase_user)
        order.with_user(self.purchase_user).button_confirm()
        self.assertEqual(order.po_review_state, 'blocked')
        self.assertEqual(order.po_warning_ids.warehouse_id, self.wh2)
