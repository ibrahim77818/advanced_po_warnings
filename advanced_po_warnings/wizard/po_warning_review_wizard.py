# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class POWarningReviewWizard(models.TransientModel):
    _name = 'po.warning.review.wizard'
    _description = 'Review Blocked Purchase Order'

    order_ids = fields.Many2many('purchase.order', string='Purchase Orders', required=True,
                                 domain=[('po_review_state', '=', 'blocked')])
    warning_ids = fields.Many2many('purchase.order.warning', compute='_compute_warning_ids',
                                   string='Warnings')
    note = fields.Text(string='Justification')

    @api.depends('order_ids')
    def _compute_warning_ids(self):
        for wizard in self:
            wizard.warning_ids = wizard.order_ids.po_warning_ids

    def _get_note(self):
        self.ensure_one()
        note = (self.note or '').strip()
        if not note:
            raise UserError(_('Please record a justification before taking a decision.'))
        return note

    def action_bypass(self):
        self.order_ids._po_warning_review_bypass(self._get_note())
        return {'type': 'ir.actions.act_window_close'}

    def action_reject(self):
        self.order_ids._po_warning_review_reject(self._get_note())
        return {'type': 'ir.actions.act_window_close'}
