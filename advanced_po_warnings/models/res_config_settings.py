# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    po_warning_enabled = fields.Boolean(related='company_id.po_warning_enabled', readonly=False)
    po_warning_user_ids = fields.Many2many(related='company_id.po_warning_user_ids', readonly=False)

    po_dup_active = fields.Boolean(related='company_id.po_dup_active', readonly=False)
    po_dup_vendor_scope = fields.Selection(related='company_id.po_dup_vendor_scope', readonly=False)
    po_dup_days = fields.Integer(related='company_id.po_dup_days', readonly=False)
    po_dup_match_qty = fields.Boolean(related='company_id.po_dup_match_qty', readonly=False)
    po_dup_mode = fields.Selection(related='company_id.po_dup_mode', readonly=False)

    po_freq_active = fields.Boolean(related='company_id.po_freq_active', readonly=False)
    po_freq_vendor_scope = fields.Selection(related='company_id.po_freq_vendor_scope', readonly=False)
    po_freq_doc_scope = fields.Selection(related='company_id.po_freq_doc_scope', readonly=False)
    po_freq_days = fields.Integer(related='company_id.po_freq_days', readonly=False)
    po_freq_min_orders = fields.Integer(related='company_id.po_freq_min_orders', readonly=False)
    po_freq_mode = fields.Selection(related='company_id.po_freq_mode', readonly=False)

    po_dest_active = fields.Boolean(related='company_id.po_dest_active', readonly=False)
    po_dest_target = fields.Selection(related='company_id.po_dest_target', readonly=False)
    po_dest_location_ids = fields.Many2many(related='company_id.po_dest_location_ids', readonly=False)
    po_dest_warehouse_ids = fields.Many2many(related='company_id.po_dest_warehouse_ids', readonly=False)
    po_dest_vendor_scope = fields.Selection(related='company_id.po_dest_vendor_scope', readonly=False)
    po_dest_doc_scope = fields.Selection(related='company_id.po_dest_doc_scope', readonly=False)
    po_dest_days = fields.Integer(related='company_id.po_dest_days', readonly=False)
    po_dest_mode = fields.Selection(related='company_id.po_dest_mode', readonly=False)
