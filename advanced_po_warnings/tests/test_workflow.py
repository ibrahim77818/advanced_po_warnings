# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged

from .common import BASE_DATE, POWarningCommon


@tagged('post_install', '-at_install', 'po_warnings')
class TestPOWarningWorkflow(POWarningCommon):

    def setUp(self):
        super().setUp()
        self.configure(po_dup_active=True, po_dup_vendor_scope='same', po_dup_days=10,
                       po_dup_match_qty=True, po_dup_mode='block',
                       po_warning_user_ids=[(6, 0, self.supervisor.ids)])
        self.activity_type = self.env.ref('advanced_po_warnings.mail_activity_type_po_warning_review')
        self.original = self.make_po(self.vendor_a, [(self.p1, 5)], confirm=True)

    def _blocked_order(self, qty=5):
        order = self.make_po(self.vendor_a, [(self.p1, qty)], user=self.purchase_user)
        order.with_user(self.purchase_user).button_confirm()
        self.assertEqual(order.po_review_state, 'blocked')
        return order

    def _review(self, order, user, note, decision):
        wizard = self.env['po.warning.review.wizard'].with_user(user).create({
            'order_ids': [(6, 0, order.ids)], 'note': note})
        return getattr(wizard, decision)()

    def _review_activities(self, order):
        return order.activity_ids.filtered(lambda a: a.activity_type_id == self.activity_type)

    # ------------------------------------------------------------- activities
    def test_activity_for_configured_users(self):
        order = self._blocked_order()
        activities = self._review_activities(order)
        self.assertEqual(activities.user_id, self.supervisor)
        self.assertIn(order.name, activities.summary)
        self.assertIn('Same products and quantities', activities.note)

    def test_activity_falls_back_to_all_supervisors(self):
        self.company.po_warning_user_ids = [(5, 0, 0)]
        order = self._blocked_order()
        expected = self.supervisor_group.user_ids.filtered(
            lambda u: u.active and not u.share and self.company in u.company_ids)
        self.assertEqual(self._review_activities(order).user_id, expected)
        self.assertIn(self.supervisor_2, expected)

    def test_configured_user_without_group_is_ignored(self):
        self.company.po_warning_user_ids = [(6, 0, (self.supervisor | self.purchase_user).ids)]
        order = self._blocked_order()
        self.assertEqual(self._review_activities(order).user_id, self.supervisor)

    def test_repeated_attempts_do_not_duplicate_activities(self):
        order = self._blocked_order()
        order.with_user(self.purchase_user).button_confirm()
        order.with_user(self.purchase_user).button_confirm()
        self.assertEqual(len(self._review_activities(order)), 1)
        self.assertEqual(order.state, 'draft')

    # ---------------------------------------------------------------- bypass
    def test_supervisor_bypass_confirms_order(self):
        order = self._blocked_order()
        self.assertTrue(order.with_user(self.supervisor).action_open_po_warning_review())
        self._review(order, self.supervisor, 'Emergency stock-out', 'action_bypass')
        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.po_review_state, 'bypassed')
        self.assertEqual(order.po_review_note, 'Emergency stock-out')
        self.assertEqual(order.po_reviewed_by_id, self.supervisor)
        self.assertTrue(order.po_reviewed_date)
        self.assertFalse(self._review_activities(order), 'review activity is marked done')
        done = self.env['mail.message'].search([
            ('model', '=', 'purchase.order'), ('res_id', '=', order.id),
            ('mail_activity_type_id', '=', self.activity_type.id)])
        self.assertTrue(done, 'activity completion is logged in the chatter')
        bodies = ' '.join(order.message_ids.mapped('body'))
        self.assertIn('bypassed by Supervisor One', bodies)
        self.assertIn('Confirmed after supervisor bypass', bodies)

    def test_bypass_respects_double_validation(self):
        self.company.write({'po_double_validation': 'two_step', 'po_double_validation_amount': 0})
        order = self._blocked_order()
        self._review(order, self.supervisor, 'ok', 'action_bypass')
        self.assertEqual(order.state, 'to approve', 'a supervisor is not a purchase manager')
        self.assertEqual(order.po_review_state, 'bypassed')

    def test_justification_is_required(self):
        order = self._blocked_order()
        for note in (False, '   '):
            with self.subTest(note=note), self.assertRaises(UserError):
                self._review(order, self.supervisor, note, 'action_bypass')
        self.assertEqual(order.po_review_state, 'blocked')

    def test_non_supervisor_cannot_review(self):
        order = self._blocked_order()
        with self.assertRaises(AccessError):
            self._review(order, self.purchase_user, 'let me through', 'action_bypass')
        with self.assertRaises(AccessError):
            order.with_user(self.purchase_user)._po_warning_review_bypass('let me through')
        with self.assertRaises(AccessError):
            order.with_user(self.purchase_user)._po_warning_review_reject('nope')
        with self.assertRaises(AccessError):
            order.with_user(self.purchase_user).action_open_po_warning_review()
        self.assertEqual(order.po_review_state, 'blocked')

    def test_only_blocked_orders_can_be_reviewed(self):
        with self.assertRaises(UserError):
            self.original.with_user(self.supervisor)._po_warning_review_bypass('x')
        with self.assertRaises(UserError):
            self.original.with_user(self.supervisor).action_open_po_warning_review()

    # ---------------------------------------------------------------- reject
    def test_supervisor_reject_cancels_order(self):
        order = self._blocked_order()
        self._review(order, self.supervisor, 'Duplicate of the original order', 'action_reject')
        self.assertEqual(order.state, 'cancel')
        self.assertEqual(order.po_review_state, 'rejected')
        self.assertEqual(order.po_review_note, 'Duplicate of the original order')
        self.assertFalse(self._review_activities(order))
        self.assertIn('rejected by Supervisor One', order.message_ids[0].body)

    def test_draft_after_rejection_requires_new_review(self):
        order = self._blocked_order()
        self._review(order, self.supervisor, 'no', 'action_reject')
        order.with_user(self.purchase_user).button_draft()
        self.assertEqual(order.po_review_state, 'none')
        order.with_user(self.purchase_user).button_confirm()
        self.assertEqual(order.po_review_state, 'blocked')
        self.assertEqual(len(self._review_activities(order)), 1)

    # --------------------------------------------------------- self-healing
    def test_corrected_order_is_unblocked_on_confirm(self):
        order = self._blocked_order()
        order.order_line.product_qty = 7  # no longer a duplicate
        order.with_user(self.purchase_user).button_confirm()
        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.po_review_state, 'none')
        self.assertFalse(order.po_warning_ids)
        self.assertFalse(self._review_activities(order))

    def test_rule_switched_to_warning_unblocks(self):
        order = self._blocked_order()
        self.company.po_dup_mode = 'warning'
        order.with_user(self.purchase_user).button_confirm()
        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.po_review_state, 'none')
        self.assertEqual(order.po_warning_ids.mode, 'warning')
        self.assertFalse(self._review_activities(order))

    def test_manual_cancel_of_blocked_order_closes_review(self):
        order = self._blocked_order()
        order.with_user(self.purchase_user).button_cancel()
        self.assertEqual(order.state, 'cancel')
        self.assertEqual(order.po_review_state, 'none')
        self.assertFalse(self._review_activities(order))

    def test_mixed_batch_confirmation(self):
        clean = self.make_po(self.vendor_b, [(self.p2, 1)], user=self.purchase_user)
        dup = self.make_po(self.vendor_a, [(self.p1, 5)], user=self.purchase_user)
        action = (clean | dup).with_user(self.purchase_user).button_confirm()
        self.assertEqual(clean.state, 'purchase')
        self.assertEqual(dup.state, 'draft')
        self.assertEqual(dup.po_review_state, 'blocked')
        self.assertEqual(action['params']['type'], 'danger')
        self.assertIn(dup.name, action['params']['message'])
        self.assertNotIn(clean.name, action['params']['message'])

    def test_mixed_warning_and_block_rules(self):
        self.company.write({'po_freq_active': True, 'po_freq_mode': 'warning', 'po_freq_days': 10,
                            'po_freq_doc_scope': 'confirmed', 'po_freq_vendor_scope': 'any',
                            'po_freq_min_orders': 1})
        order = self._blocked_order()
        self.assertEqual(sorted(order.po_warning_ids.mapped('mode')), ['block', 'warning'])
        self._review(order, self.supervisor, 'ok', 'action_bypass')
        self.assertEqual(order.state, 'purchase')

    def test_already_confirmed_orders_are_not_rechecked(self):
        self.assertTrue(self.original.button_confirm())
        self.assertEqual(self.original.po_review_state, 'none')

    # ---------------------------------------------------------------- preview
    def test_preview_on_saved_order(self):
        order = self.make_po(self.vendor_a, [(self.p1, 5)])
        self.assertEqual(order.po_warning_preview_level, 'block')
        self.assertIn('will be blocked', order.po_warning_preview)
        self.assertIn(self.original.name, order.po_warning_preview)
        order.order_line.product_qty = 6
        self.assertEqual(order.po_warning_preview_level, 'none')
        self.assertFalse(order.po_warning_preview)

    def test_preview_on_unsaved_form(self):
        form = Form(self.env['purchase.order'].with_user(self.purchase_user))
        form.partner_id = self.vendor_a
        form.date_order = BASE_DATE
        with form.order_line.new() as line:
            line.product_id = self.p1
            line.product_qty = 5
        self.assertEqual(form.po_warning_preview_level, 'block')
        with form.order_line.edit(0) as line:
            line.product_qty = 4
        self.assertEqual(form.po_warning_preview_level, 'none')

    def test_chatter_layout_fits_side_panel(self):
        order = self._blocked_order()
        note = order.message_ids[0].body
        self.assertIn('o_po_warning_list', note, 'chatter notes use the stacked layout')
        self.assertNotIn('<table', note)
        self.assertIn('/odoo/purchase.order/%s' % self.original.id, note)
        activity = self._review_activities(order)
        self.assertIn('o_po_warning_list', activity.note)
        preview_order = self.make_po(self.vendor_a, [(self.p1, 5)])
        self.assertIn('<table', preview_order.po_warning_preview, 'the full-width preview keeps the table')
