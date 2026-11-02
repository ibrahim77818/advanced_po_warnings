# -*- coding: utf-8 -*-
from odoo import fields, models

from .res_company import ENFORCEMENT_MODE

WARNING_RULES = [
    ('duplicate', 'Order Duplicate'),
    ('frequency', 'Item Reorder Frequency'),
    ('destination', 'Destination'),
]


class PurchaseOrderWarning(models.Model):
    """Audit record of a warning raised when an order was submitted for confirmation."""
    _name = 'purchase.order.warning'
    _description = 'Purchase Order Warning'
    _order = 'order_id desc, id'
    _rec_name = 'message'

    order_id = fields.Many2one('purchase.order', string='Purchase Order', required=True,
                               index=True, ondelete='cascade', readonly=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True, index=True)
    partner_id = fields.Many2one(related='order_id.partner_id', string='Vendor')
    review_state = fields.Selection(related='order_id.po_review_state')
    rule = fields.Selection(WARNING_RULES, string='Rule', required=True, readonly=True)
    mode = fields.Selection(ENFORCEMENT_MODE, string='Enforcement', required=True, readonly=True)
    product_id = fields.Many2one('product.product', string='Product', readonly=True)
    location_id = fields.Many2one('stock.location', string='Destination Location', readonly=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Destination Warehouse', readonly=True)
    conflict_order_ids = fields.Many2many(
        'purchase.order', 'purchase_order_warning_conflict_rel', 'warning_id', 'order_id',
        string='Related Orders', readonly=True)
    message = fields.Text(string='Alert', required=True, readonly=True)
