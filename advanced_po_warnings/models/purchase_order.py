# -*- coding: utf-8 -*-
"""Advanced purchase order warnings: rule engine and confirmation workflow.

Performance notes
-----------------
Every rule is evaluated for a whole recordset with a single SQL statement per company
(the current orders are passed in as arrays and ``unnest``-ed), so the number of
queries does not grow with the number of orders being confirmed (no N+1). The current
orders are read from the ORM cache, which also lets the form preview work on unsaved
changes.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.sql import create_index


STATES_ALL_ACTIVE = ['draft', 'sent', 'to approve', 'purchase', 'done']
STATES_CONFIRMED = ['to approve', 'purchase', 'done']
REVIEW_FIELDS = {'po_review_state', 'po_review_note', 'po_reviewed_by_id', 'po_reviewed_date'}
QTY_QUANT = Decimal('0.0001')

# Vendor-scope SQL fragments. Fixed strings only (never user input) so the planner can
# pick the best join strategy for each case.
VENDOR_SCOPE_SQL = {
    'same': 'AND cand_partner = cur.partner_id',
    'different': 'AND cand_partner <> cur.partner_id',
    'any': '',
}


def _qty_key(qty):
    """Quantity rounded the same way as ``ROUND(x::numeric, 4)`` in PostgreSQL."""
    return Decimal(repr(float(qty or 0.0))).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    po_review_state = fields.Selection([
        ('none', 'Not Required'),
        ('blocked', 'Blocked'),
        ('bypassed', 'Bypassed'),
        ('rejected', 'Rejected'),
    ], string='Warning Review', default='none', required=True, copy=False, index=True,
        readonly=True, tracking=True)
    po_review_note = fields.Text(string='Review Justification', copy=False, readonly=True)
    po_reviewed_by_id = fields.Many2one('res.users', string='Reviewed By', copy=False, readonly=True)
    po_reviewed_date = fields.Datetime(string='Reviewed On', copy=False, readonly=True)
    po_warning_ids = fields.One2many('purchase.order.warning', 'order_id', string='Order Warnings',
                                     readonly=True, copy=False)
    po_warning_count = fields.Integer(compute='_compute_po_warning_count', string='Warnings')
    po_warning_preview = fields.Html(compute='_compute_po_warning_preview', sanitize=False,
                                     string='Warning Preview')
    po_warning_preview_level = fields.Selection([
        ('none', 'None'), ('warning', 'Warning'), ('block', 'Blocking'),
    ], compute='_compute_po_warning_preview', string='Preview Level')

    def init(self):
        super().init()
        # Supports the period filter used by every rule (company + date window).
        create_index(self._cr, 'purchase_order_po_warning_company_date_idx', self._table,
                     ['company_id', 'date_order'])

    # ------------------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------------------
    def _compute_po_warning_count(self):
        data = self.env['purchase.order.warning']._read_group(
            [('order_id', 'in', self.ids)], ['order_id'], ['order_id'])
        counts = {d['order_id'][0]: d['order_id_count'] for d in data}
        for order in self:
            order.po_warning_count = counts.get(order.id, 0)

    @api.depends('partner_id', 'date_order', 'company_id', 'state', 'picking_type_id',
                 'order_line.product_id', 'order_line.product_qty', 'order_line.product_uom',
                 'order_line.display_type')
    def _compute_po_warning_preview(self):
        todo = self.filtered(lambda o: o.state in ('draft', 'sent') and o.company_id.po_warning_enabled)
        (self - todo).update({'po_warning_preview': False, 'po_warning_preview_level': 'none'})
        if not todo:
            return
        for order, hits in zip(todo, todo._po_warning_evaluate()):
            if not hits:
                order.po_warning_preview = False
                order.po_warning_preview_level = 'none'
                continue
            blocking = any(h['mode'] == 'block' for h in hits)
            order.po_warning_preview_level = 'block' if blocking else 'warning'
            order.po_warning_preview = order._po_warning_render(hits, context='preview')

    # ------------------------------------------------------------------------------
    # Rule engine
    # ------------------------------------------------------------------------------
    def _po_warning_snapshot(self):
        """Read each order from memory (works for unsaved records in the form view).

        :return: list of dicts aligned with ``self``; orders that cannot be evaluated
                 (no vendor) are returned as ``None``.
        """
        snaps = []
        for order in self:
            if not order.partner_id:
                snaps.append(None)
                continue
            qty_by_product = defaultdict(float)
            for line in order.order_line:
                if line.display_type or not line.product_id:
                    continue
                qty_by_product[line.product_id.id] += line.product_uom_qty
            picking_type = order.picking_type_id
            snaps.append({
                'order_id': order._origin.id or 0,
                'company': order.company_id or self.env.company,
                'partner_id': order.partner_id.commercial_partner_id.id,
                'date': order.date_order or fields.Datetime.now(),
                'qty_by_product': dict(qty_by_product),
                'location': picking_type.default_location_dest_id,
                'warehouse': picking_type.warehouse_id,
            })
        return snaps

    def _po_warning_evaluate(self):
        """Evaluate every enabled rule for the orders in ``self``.

        :return: list (aligned with ``self``) of lists of hit dicts with keys
                 ``rule, mode, message, product_id, location_id, warehouse_id, conflict_ids``.
        """
        if not self.env.lang:
            # Server-side calls (cron, scripts) may carry no language: pin the user's one so
            # translated messages and selection labels never end up in mixed languages.
            return self.with_context(lang=self.env.user.lang)._po_warning_evaluate()
        results = [[] for _i in self]
        snaps = self._po_warning_snapshot()
        by_company = defaultdict(list)
        for idx, snap in enumerate(snaps):
            if snap and snap['company'].po_warning_enabled:
                by_company[snap['company']].append((idx, snap))
        for company, entries in by_company.items():
            if company.po_dup_active:
                self._po_warning_rule_duplicate(company, entries, results)
            if company.po_freq_active:
                self._po_warning_rule_frequency(company, entries, results)
            if company.po_dest_active:
                self._po_warning_rule_destination(company, entries, results)
        return results

    @api.model
    def _po_warning_labels(self, field_name):
        field = self.env['res.company']._fields[field_name]
        return dict(field._description_selection(self.env))

    @api.model
    def _po_warning_window(self, entries, days):
        dates = [snap['date'] for _idx, snap in entries]
        return min(dates) - timedelta(days=days), max(dates) + timedelta(days=days)

    def _po_warning_rule_duplicate(self, company, entries, results):
        """Same product set (and quantities) on another non-cancelled order."""
        entries = [(idx, snap) for idx, snap in entries if snap['qty_by_product']]
        if not entries:
            return
        days, match_qty = company.po_dup_days, company.po_dup_match_qty
        head_keys, head_orders, head_partners, head_dates = [], [], [], []
        line_keys, line_products, line_qtys, line_counts = [], [], [], []
        for idx, snap in entries:
            head_keys.append(idx)
            head_orders.append(snap['order_id'])
            head_partners.append(snap['partner_id'])
            head_dates.append(snap['date'])
            line_counts.append(len(snap['qty_by_product']))
            for product_id, qty in snap['qty_by_product'].items():
                line_keys.append(idx)
                line_products.append(product_id)
                line_qtys.append(_qty_key(qty))
        date_from, date_to = self._po_warning_window(entries, days)
        query = """
            WITH cur AS (
                SELECT * FROM unnest(%(head_keys)s::int[], %(head_orders)s::int[],
                                     %(head_partners)s::int[], %(head_dates)s::timestamp[],
                                     %(line_counts)s::int[])
                       AS h(key, order_id, partner_id, date_ref, n_products)
            ),
            cur_lines AS (
                SELECT * FROM unnest(%(line_keys)s::int[], %(line_products)s::int[],
                                     %(line_qtys)s::numeric[]) AS l(key, product_id, qty)
            ),
            cand AS (
                SELECT po.id, po.date_order, rp.commercial_partner_id AS cand_partner
                  FROM purchase_order po
                  JOIN res_partner rp ON rp.id = po.partner_id
                 WHERE po.company_id = %(company_id)s
                   AND po.state = ANY(%(states)s)
                   AND po.date_order BETWEEN %(date_from)s AND %(date_to)s
            ),
            cand_lines AS (
                SELECT pol.order_id, pol.product_id,
                       ROUND(SUM(pol.product_uom_qty)::numeric, 4) AS qty
                  FROM purchase_order_line pol
                  JOIN cand ON cand.id = pol.order_id
                 WHERE pol.display_type IS NULL AND pol.product_id IS NOT NULL
                 GROUP BY pol.order_id, pol.product_id
            ),
            cand_n AS (
                SELECT order_id, COUNT(*) AS n_products FROM cand_lines GROUP BY order_id
            ),
            pairs AS (
                SELECT cur.key, cur.n_products, cand.id AS cand_id, cand.date_order
                  FROM cur
                  JOIN cand ON cand.id <> cur.order_id
                           AND cand.date_order BETWEEN cur.date_ref - %(days)s * interval '1 day'
                                                   AND cur.date_ref + %(days)s * interval '1 day'
                           {vendor_scope}
                  JOIN cand_n ON cand_n.order_id = cand.id AND cand_n.n_products = cur.n_products
            )
            SELECT pairs.key, array_agg(DISTINCT pairs.cand_id)
              FROM pairs
              JOIN cur_lines cl ON cl.key = pairs.key
              JOIN cand_lines cdl ON cdl.order_id = pairs.cand_id
                                 AND cdl.product_id = cl.product_id
                                 AND (NOT %(match_qty)s OR cdl.qty = cl.qty)
             GROUP BY pairs.key, pairs.cand_id, pairs.n_products
            HAVING COUNT(*) = pairs.n_products
        """.format(vendor_scope=VENDOR_SCOPE_SQL[company.po_dup_vendor_scope])
        self.env.flush_all()
        self.env.cr.execute(query, {
            'head_keys': head_keys, 'head_orders': head_orders, 'head_partners': head_partners,
            'head_dates': head_dates, 'line_counts': line_counts, 'line_keys': line_keys,
            'line_products': line_products, 'line_qtys': line_qtys,
            'company_id': company.id, 'states': STATES_ALL_ACTIVE,
            'date_from': date_from, 'date_to': date_to, 'days': days, 'match_qty': match_qty,
        })
        conflicts = defaultdict(set)
        for key, cand_ids in self.env.cr.fetchall():
            conflicts[key].update(cand_ids)
        if not conflicts:
            return
        scope = self._po_warning_labels('po_dup_vendor_scope')[company.po_dup_vendor_scope]
        if match_qty:
            message = _('Same products and quantities as another order within %(days)s days (%(scope)s).',
                        days=days, scope=scope)
        else:
            message = _('Same products as another order within %(days)s days (%(scope)s).',
                        days=days, scope=scope)
        for key, cand_ids in conflicts.items():
            results[key].append({
                'rule': 'duplicate', 'mode': company.po_dup_mode, 'message': message,
                'product_id': False, 'location_id': False, 'warehouse_id': False,
                'conflict_ids': sorted(cand_ids),
            })

    def _po_warning_rule_frequency(self, company, entries, results):
        """Products already present on N+ other orders within the time window."""
        keys, orders, partners, dates, products = [], [], [], [], []
        for idx, snap in entries:
            for product_id in snap['qty_by_product']:
                keys.append(idx)
                orders.append(snap['order_id'])
                partners.append(snap['partner_id'])
                dates.append(snap['date'])
                products.append(product_id)
        if not keys:
            return
        days = company.po_freq_days
        states = STATES_CONFIRMED if company.po_freq_doc_scope == 'confirmed' else STATES_ALL_ACTIVE
        date_from, date_to = self._po_warning_window(entries, days)
        query = """
            WITH cur AS (
                SELECT * FROM unnest(%(keys)s::int[], %(orders)s::int[], %(partners)s::int[],
                                     %(dates)s::timestamp[], %(products)s::int[])
                       AS c(key, order_id, partner_id, date_ref, product_id)
            ),
            cand AS (
                SELECT pol.product_id, po.id, po.date_order,
                       rp.commercial_partner_id AS cand_partner
                  FROM purchase_order_line pol
                  JOIN purchase_order po ON po.id = pol.order_id
                  JOIN res_partner rp ON rp.id = po.partner_id
                 WHERE pol.product_id = ANY(%(products)s)
                   AND pol.display_type IS NULL
                   AND po.company_id = %(company_id)s
                   AND po.state = ANY(%(states)s)
                   AND po.date_order BETWEEN %(date_from)s AND %(date_to)s
            )
            SELECT cur.key, cur.product_id, array_agg(DISTINCT cand.id)
              FROM cur
              JOIN cand ON cand.product_id = cur.product_id
                       AND cand.id <> cur.order_id
                       AND cand.date_order BETWEEN cur.date_ref - %(days)s * interval '1 day'
                                               AND cur.date_ref + %(days)s * interval '1 day'
                       {vendor_scope}
             GROUP BY cur.key, cur.product_id
            HAVING COUNT(DISTINCT cand.id) >= %(min_orders)s
        """.format(vendor_scope=VENDOR_SCOPE_SQL[company.po_freq_vendor_scope])
        self.env.flush_all()
        self.env.cr.execute(query, {
            'keys': keys, 'orders': orders, 'partners': partners, 'dates': dates,
            'products': products, 'company_id': company.id, 'states': states,
            'date_from': date_from, 'date_to': date_to, 'days': days,
            'min_orders': company.po_freq_min_orders,
        })
        rows = self.env.cr.fetchall()
        if not rows:
            return
        scope = self._po_warning_labels('po_freq_vendor_scope')[company.po_freq_vendor_scope]
        docs = self._po_warning_labels('po_freq_doc_scope')[company.po_freq_doc_scope]
        product_names = {
            p.id: p.display_name
            for p in self.env['product.product'].browse({r[1] for r in rows}).with_context(
                display_default_code=True)
        }
        for key, product_id, cand_ids in rows:
            results[key].append({
                'rule': 'frequency', 'mode': company.po_freq_mode,
                'message': _('%(product)s was already ordered %(count)s time(s) within %(days)s days '
                             '(%(scope)s, %(docs)s).',
                             product=product_names[product_id], count=len(cand_ids), days=days,
                             scope=scope, docs=docs),
                'product_id': product_id, 'location_id': False, 'warehouse_id': False,
                'conflict_ids': sorted(cand_ids),
            })

    def _po_warning_rule_destination(self, company, entries, results):
        """Orders delivered to a monitored location / warehouse within the time window."""
        target = company.po_dest_target
        monitored = []
        if target == 'location':
            watched = company.po_dest_location_ids
            watched_ids = {str(loc_id) for loc_id in watched.ids}
            for idx, snap in entries:
                loc = snap['location']
                if not loc:
                    continue
                if not watched or watched_ids & set((loc.parent_path or '').split('/')):
                    monitored.append((idx, snap, loc.id))
        else:
            watched = company.po_dest_warehouse_ids
            for idx, snap in entries:
                wh = snap['warehouse']
                if wh and (not watched or wh in watched):
                    monitored.append((idx, snap, wh.id))
        if not monitored:
            return

        days = company.po_dest_days
        dest_model = 'stock.location' if target == 'location' else 'stock.warehouse'
        dest_names = {d.id: d.display_name for d in self.env[dest_model].browse({m[2] for m in monitored})}
        mode = company.po_dest_mode
        field = 'location_id' if target == 'location' else 'warehouse_id'

        def _hit(idx, dest_id, message, conflict_ids):
            hit = {'rule': 'destination', 'mode': mode, 'message': message, 'product_id': False,
                   'location_id': False, 'warehouse_id': False, 'conflict_ids': conflict_ids}
            hit[field] = dest_id
            results[idx].append(hit)

        if days == 0:
            # Restricted destination: every order delivered there is flagged.
            for idx, _snap, dest_id in monitored:
                _hit(idx, dest_id, _('Deliveries to %(dest)s are restricted.', dest=dest_names[dest_id]), [])
            return

        states = STATES_CONFIRMED if company.po_dest_doc_scope == 'confirmed' else STATES_ALL_ACTIVE
        date_from, date_to = self._po_warning_window([(m[0], m[1]) for m in monitored], days)
        dest_column = 'spt.default_location_dest_id' if target == 'location' else 'spt.warehouse_id'
        query = """
            WITH cur AS (
                SELECT * FROM unnest(%(keys)s::int[], %(orders)s::int[], %(partners)s::int[],
                                     %(dates)s::timestamp[], %(dests)s::int[])
                       AS c(key, order_id, partner_id, date_ref, dest_id)
            ),
            cand AS (
                SELECT po.id, po.date_order, {dest_column} AS dest_id,
                       rp.commercial_partner_id AS cand_partner
                  FROM purchase_order po
                  JOIN stock_picking_type spt ON spt.id = po.picking_type_id
                  JOIN res_partner rp ON rp.id = po.partner_id
                 WHERE po.company_id = %(company_id)s
                   AND po.state = ANY(%(states)s)
                   AND po.date_order BETWEEN %(date_from)s AND %(date_to)s
                   AND {dest_column} = ANY(%(dests)s)
            )
            SELECT cur.key, array_agg(DISTINCT cand.id)
              FROM cur
              JOIN cand ON cand.dest_id = cur.dest_id
                       AND cand.id <> cur.order_id
                       AND cand.date_order BETWEEN cur.date_ref - %(days)s * interval '1 day'
                                               AND cur.date_ref + %(days)s * interval '1 day'
                       {vendor_scope}
             GROUP BY cur.key
        """.format(dest_column=dest_column,
                   vendor_scope=VENDOR_SCOPE_SQL[company.po_dest_vendor_scope])
        self.env.flush_all()
        self.env.cr.execute(query, {
            'keys': [m[0] for m in monitored], 'orders': [m[1]['order_id'] for m in monitored],
            'partners': [m[1]['partner_id'] for m in monitored],
            'dates': [m[1]['date'] for m in monitored], 'dests': [m[2] for m in monitored],
            'company_id': company.id, 'states': states, 'date_from': date_from,
            'date_to': date_to, 'days': days,
        })
        rows = dict(self.env.cr.fetchall())
        if not rows:
            return
        scope = self._po_warning_labels('po_dest_vendor_scope')[company.po_dest_vendor_scope]
        for idx, _snap, dest_id in monitored:
            if idx in rows:
                _hit(idx, dest_id,
                     _('Another order is already scheduled for %(dest)s within %(days)s days (%(scope)s).',
                       dest=dest_names[dest_id], days=days, scope=scope),
                     sorted(rows[idx]))

    # ------------------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------------------
    def _po_warning_render(self, hits, context='message', title=None):
        """Render alerts as HTML (QWeb template, translated in the user's language)."""
        if not self.env.lang:
            return self.with_context(lang=self.env.user.lang)._po_warning_render(
                hits, context=context, title=title)
        Warning = self.env['purchase.order.warning']
        rule_labels = dict(Warning._fields['rule']._description_selection(self.env))
        mode_labels = dict(Warning._fields['mode']._description_selection(self.env))
        conflict_ids = {cid for h in hits for cid in h['conflict_ids']}
        conflicts = {o.id: o for o in self.env['purchase.order'].sudo().browse(conflict_ids)}
        rows = [{
            'rule': rule_labels.get(h['rule'], h['rule']),
            'mode': h['mode'],
            'mode_label': mode_labels.get(h['mode'], h['mode']),
            'message': h['message'],
            'conflicts': [conflicts[cid] for cid in h['conflict_ids'] if cid in conflicts],
        } for h in hits]
        blocking = any(h['mode'] == 'block' for h in hits)
        return self.env['ir.qweb']._render('advanced_po_warnings.po_warning_alerts', {
            'rows': rows, 'blocking': blocking, 'context': context, 'title': title,
        })

    # ------------------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------------------
    def _po_warning_store(self, hits):
        """Replace the stored warnings of this order with the given hits."""
        self.ensure_one()
        Warning = self.env['purchase.order.warning'].sudo()
        Warning.search([('order_id', '=', self.id)]).unlink()
        Warning.create([{
            'order_id': self.id, 'rule': h['rule'], 'mode': h['mode'], 'message': h['message'],
            'product_id': h['product_id'], 'location_id': h['location_id'],
            'warehouse_id': h['warehouse_id'], 'conflict_order_ids': [(6, 0, h['conflict_ids'])],
        } for h in hits])

    def _po_warning_reviewers(self):
        """Users who receive the review activity for this order's company."""
        self.ensure_one()
        company = self.company_id
        group = self.env.ref('advanced_po_warnings.group_po_warning_supervisor')
        supervisors = group.sudo().users.filtered(
            lambda u: u.active and not u.share and company in u.company_ids)
        configured = company.po_warning_user_ids & supervisors
        return configured or supervisors

    def _po_warning_activity_type(self):
        return self.env.ref('advanced_po_warnings.mail_activity_type_po_warning_review')

    def _po_warning_open_activities(self):
        return self.env['mail.activity'].sudo().search([
            ('res_model', '=', self._name), ('res_id', 'in', self.ids),
            ('activity_type_id', '=', self._po_warning_activity_type().id),
        ])

    def _po_warning_close_activities(self, feedback):
        activities = self._po_warning_open_activities()
        if activities:
            activities.action_feedback(feedback=feedback)

    def _po_warning_block(self, hits):
        """Halt confirmation: flag the order, log the alerts and notify the supervisors."""
        self.ensure_one()
        self.sudo().write({'po_review_state': 'blocked', 'po_review_note': False,
                           'po_reviewed_by_id': False, 'po_reviewed_date': False})
        body = self._po_warning_render(hits, title=_('Order confirmation blocked'))
        self.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')
        if self._po_warning_open_activities():
            return  # supervisors were already notified for this order
        users = self._po_warning_reviewers()
        if not users:
            return
        activity_type = self._po_warning_activity_type()
        model_id = self.env['ir.model']._get_id(self._name)
        deadline = fields.Date.context_today(self)
        if activity_type.delay_count:
            deadline += timedelta(days=activity_type.delay_count)
        self.env['mail.activity'].sudo().create([{
            'res_model_id': model_id,
            'res_id': self.id,
            'activity_type_id': activity_type.id,
            'summary': _('Review blocked purchase order %s', self.name),
            'note': body,
            'user_id': user.id,
            'date_deadline': deadline,
        } for user in users])

    def _po_warning_unblock(self):
        """The order no longer triggers blocking rules (it was corrected)."""
        self.sudo().write({'po_review_state': 'none'})
        self._po_warning_close_activities(_('Blocking warnings were resolved before confirmation.'))

    def button_confirm(self):
        to_check = self.filtered(lambda o: o.state in ('draft', 'sent') and o.company_id.po_warning_enabled)
        if not to_check:
            return super().button_confirm()

        blocked = self.browse()
        warned = []
        for order, hits in zip(to_check, to_check._po_warning_evaluate()):
            if not hits:
                if order.po_review_state == 'blocked':
                    order._po_warning_unblock()
                order.sudo().po_warning_ids.unlink()
                continue
            order._po_warning_store(hits)
            is_blocking = any(h['mode'] == 'block' for h in hits)
            if is_blocking and order.po_review_state != 'bypassed':
                order._po_warning_block(hits)
                blocked |= order
            else:
                if order.po_review_state == 'blocked':
                    order._po_warning_unblock()
                warned.append((order, hits))

        to_confirm = self - blocked
        res = super(PurchaseOrder, to_confirm).button_confirm() if to_confirm else True

        for order, hits in warned:
            title = (_('Confirmed after supervisor bypass') if order.po_review_state == 'bypassed'
                     else _('Confirmed with warnings'))
            order.message_post(body=order._po_warning_render(hits, title=title),
                               message_type='comment', subtype_xmlid='mail.mt_note')

        if blocked:
            return self._po_warning_notification(
                _('Confirmation blocked'),
                _('%(orders)s could not be confirmed. Supervisors have been notified for review.',
                  orders=', '.join(blocked.mapped('name'))),
                'danger')
        if warned and not self.env.context.get('po_warning_silent'):
            return self._po_warning_notification(
                _('Confirmed with warnings'),
                _('%(orders)s confirmed. Warnings were recorded in the chatter.',
                  orders=', '.join(order.name for order, _h in warned)),
                'warning')
        return res

    @api.model
    def _po_warning_notification(self, title, message, ntype):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title, 'message': message, 'type': ntype, 'sticky': ntype == 'danger',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def button_cancel(self):
        manually_cancelled = self.filtered(lambda o: o.po_review_state == 'blocked')
        res = super().button_cancel()
        if manually_cancelled:
            manually_cancelled._po_warning_close_activities(_('The order was cancelled.'))
            manually_cancelled.sudo().write({'po_review_state': 'none'})
        return res

    def button_draft(self):
        res = super().button_draft()
        reviewed = self.filtered(lambda o: o.po_review_state in ('bypassed', 'rejected'))
        if reviewed:
            # A new confirmation attempt must go through the checks again.
            reviewed.sudo().write({'po_review_state': 'none'})
        return res

    # ------------------------------------------------------------------------------
    # Supervisor review
    # ------------------------------------------------------------------------------
    @api.model
    def _po_warning_check_supervisor(self):
        if not self.env.user.has_group('advanced_po_warnings.group_po_warning_supervisor'):
            raise AccessError(_('Only PO Warnings Supervisors can review blocked purchase orders.'))

    def action_open_po_warning_review(self):
        self._po_warning_check_supervisor()
        blocked = self.filtered(lambda o: o.po_review_state == 'blocked')
        if not blocked:
            raise UserError(_('Only blocked purchase orders can be reviewed.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Review Blocked Order'),
            'res_model': 'po.warning.review.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_ids': [(6, 0, blocked.ids)]},
        }

    def _po_warning_review_bypass(self, note):
        self._po_warning_check_supervisor()
        orders = self.filtered(lambda o: o.po_review_state == 'blocked')
        if not orders:
            raise UserError(_('Only blocked purchase orders can be bypassed.'))
        orders.sudo().write({'po_review_state': 'bypassed', 'po_review_note': note,
                             'po_reviewed_by_id': self.env.uid,
                             'po_reviewed_date': fields.Datetime.now()})
        orders._po_warning_close_activities(_('Bypassed: %s', note))
        for order in orders:
            order.message_post(
                body=Markup('<p><strong>%s</strong></p><p>%s</p>') % (
                    _('Warning restriction bypassed by %s', self.env.user.name), note),
                message_type='comment', subtype_xmlid='mail.mt_note')
        orders.with_context(po_warning_silent=True).button_confirm()
        return orders

    def _po_warning_review_reject(self, note):
        self._po_warning_check_supervisor()
        orders = self.filtered(lambda o: o.po_review_state == 'blocked')
        if not orders:
            raise UserError(_('Only blocked purchase orders can be rejected.'))
        orders.sudo().write({'po_review_state': 'rejected', 'po_review_note': note,
                             'po_reviewed_by_id': self.env.uid,
                             'po_reviewed_date': fields.Datetime.now()})
        orders._po_warning_close_activities(_('Rejected: %s', note))
        orders.button_cancel()
        for order in orders:
            order.message_post(
                body=Markup('<p><strong>%s</strong></p><p>%s</p>') % (
                    _('Order rejected by %s', self.env.user.name), note),
                message_type='comment', subtype_xmlid='mail.mt_note',
                partner_ids=order.user_id.partner_id.ids)
        return orders

    # ------------------------------------------------------------------------------
    # Integrity: review fields can only be changed through the review workflow
    # ------------------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                # Default values (e.g. sent back by a client) are harmless; anything else is not.
                if any(vals.get(f) not in (False, None, 'none') for f in REVIEW_FIELDS & set(vals)):
                    raise AccessError(_('Warning review fields can only be set through the review workflow.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and REVIEW_FIELDS & set(vals):
            raise AccessError(_('Warning review fields can only be set through the review workflow.'))
        return super().write(vals)
