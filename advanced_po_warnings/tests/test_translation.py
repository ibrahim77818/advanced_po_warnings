# -*- coding: utf-8 -*-
from odoo.tests import tagged

from .common import POWarningCommon


@tagged('post_install', '-at_install', 'po_warnings')
class TestPOWarningTranslation(POWarningCommon):
    """Requires the Arabic (ar_001) language to be installed; skipped otherwise."""

    def setUp(self):
        super().setUp()
        if not self.env['res.lang']._lang_get('ar_001'):
            self.skipTest('Arabic (ar_001) is not installed')
        self.configure(po_dup_active=True, po_dup_vendor_scope='same', po_dup_days=10,
                       po_dup_match_qty=True, po_dup_mode='block')
        self.make_po(self.vendor_a, [(self.p1, 5)], confirm=True)
        self.order = self.make_po(self.vendor_a, [(self.p1, 5)])

    def test_arabic_alerts(self):
        order = self.order.with_context(lang='ar_001')
        hits = self.hits(order)
        self.assertIn('نفس المنتجات والكميات', hits[0]['message'])
        self.assertIn('نفس المورد', hits[0]['message'])
        html = order._po_warning_render(hits)
        for term in ('تكرار الأمر', 'تحذير + حظر التأكيد', 'الأوامر ذات الصلة'):
            self.assertIn(term, html)
        preview = order._po_warning_render(hits, context='preview')
        for term in ('القاعدة', 'الإجراء', 'التنبيه', 'سيُحظر هذا الأمر'):
            self.assertIn(term, preview)

    def test_no_context_language_uses_user_language_consistently(self):
        user = self.supervisor
        user.lang = 'ar_001'
        order = self.order.with_user(user).with_context(lang=None)
        hits = order._po_warning_evaluate()[0]
        html = order._po_warning_render(hits)
        self.assertIn('نفس المورد', hits[0]['message'])
        self.assertIn('الأوامر ذات الصلة', html, 'template labels follow the same language as the messages')
        self.assertNotIn('Same vendor', html)

    def test_arabic_settings_labels(self):
        fields = self.env['res.config.settings'].with_context(lang='ar_001').fields_get(
            ['po_warning_enabled', 'po_dup_active', 'po_freq_active', 'po_dest_active'], ['string'])
        self.assertEqual(fields['po_warning_enabled']['string'], 'تحذيرات أوامر الشراء المتقدمة')
        self.assertEqual(fields['po_dup_active']['string'], 'فحص تكرار الأمر')
        self.assertEqual(fields['po_freq_active']['string'], 'فحص معدل إعادة طلب الصنف')
        self.assertEqual(fields['po_dest_active']['string'], 'فحص الوجهة')
