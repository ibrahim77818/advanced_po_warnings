# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

VENDOR_SCOPE = [
    ('same', 'Same vendor'),
    ('different', 'Different vendors'),
    ('any', 'Any vendor'),
]
DOC_SCOPE = [
    ('confirmed', 'Confirmed purchase orders'),
    ('all', 'RFQs and purchase orders'),
]
ENFORCEMENT_MODE = [
    ('warning', 'Warning only'),
    ('block', 'Warning + block confirmation'),
]
DEST_TARGET = [
    ('location', 'Specific locations'),
    ('warehouse', 'Specific warehouses'),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    po_warning_enabled = fields.Boolean(string='Advanced Purchase Order Warnings')
    po_warning_user_ids = fields.Many2many(
        'res.users', 'res_company_po_warning_user_rel', 'company_id', 'user_id',
        string='Review Activity Users',
        domain=lambda self: [
            ('share', '=', False),
            ('groups_id', 'in', self.env.ref(
                'advanced_po_warnings.group_po_warning_supervisor', raise_if_not_found=False
            ).ids or [0]),
        ],
        help='Supervisors who receive the review activity when an order is blocked. '
             'Leave empty to notify every PO Warnings Supervisor of the company.')

    # --- Rule 1: order duplicate -------------------------------------------------
    po_dup_active = fields.Boolean(string='Order Duplicate Check')
    po_dup_vendor_scope = fields.Selection(VENDOR_SCOPE, string='Duplicate: Vendor Scope', default='same')
    po_dup_days = fields.Integer(string='Duplicate: Period (Days)', default=30)
    po_dup_match_qty = fields.Boolean(string='Duplicate: Match Quantities', default=True)
    po_dup_mode = fields.Selection(ENFORCEMENT_MODE, string='Duplicate: Enforcement', default='warning')

    # --- Rule 2: item reorder frequency ------------------------------------------
    po_freq_active = fields.Boolean(string='Item Reorder Frequency Check')
    po_freq_vendor_scope = fields.Selection(VENDOR_SCOPE, string='Frequency: Vendor Scope', default='any')
    po_freq_doc_scope = fields.Selection(DOC_SCOPE, string='Frequency: Documents', default='confirmed')
    po_freq_days = fields.Integer(string='Frequency: Time Window (Days)', default=30)
    po_freq_min_orders = fields.Integer(
        string='Frequency: Previous Orders Threshold', default=1,
        help='Warn when the product already appears on at least this many other orders in the window.')
    po_freq_mode = fields.Selection(ENFORCEMENT_MODE, string='Frequency: Enforcement', default='warning')

    # --- Rule 3: destination location / warehouse --------------------------------
    po_dest_active = fields.Boolean(string='Destination Check')
    po_dest_target = fields.Selection(DEST_TARGET, string='Destination: Monitor', default='warehouse')
    po_dest_location_ids = fields.Many2many(
        'stock.location', 'res_company_po_warning_location_rel', 'company_id', 'location_id',
        string='Destination: Locations',
        help='Monitored locations (their sub-locations included). Leave empty to monitor all.')
    po_dest_warehouse_ids = fields.Many2many(
        'stock.warehouse', 'res_company_po_warning_warehouse_rel', 'company_id', 'warehouse_id',
        string='Destination: Warehouses', help='Monitored warehouses. Leave empty to monitor all.')
    po_dest_vendor_scope = fields.Selection(VENDOR_SCOPE, string='Destination: Vendor Scope', default='any')
    po_dest_doc_scope = fields.Selection(DOC_SCOPE, string='Destination: Documents', default='all')
    po_dest_days = fields.Integer(
        string='Destination: Time Window (Days)', default=7,
        help='Flag the order when another order to the same destination exists within this many days. '
             'Set 0 to flag every order to a monitored destination (restricted destination).')
    po_dest_mode = fields.Selection(ENFORCEMENT_MODE, string='Destination: Enforcement', default='warning')

    @api.constrains('po_dup_days', 'po_freq_days', 'po_freq_min_orders', 'po_dest_days')
    def _check_po_warning_values(self):
        for company in self:
            if company.po_dup_days < 1 or company.po_freq_days < 1:
                raise ValidationError(_('The duplicate and frequency periods must be at least 1 day.'))
            if company.po_freq_min_orders < 1:
                raise ValidationError(_('The previous orders threshold must be at least 1.'))
            if company.po_dest_days < 0:
                raise ValidationError(_('The destination time window cannot be negative.'))
