# -*- coding: utf-8 -*-
"""Tests for ``billing.report.wizard``."""

from datetime import date, timedelta

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_billing_report")
class TestBillingReportWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Wizard = cls.env["billing.report.wizard"]

    # ------------------------------------------------------------------
    # default_get
    # ------------------------------------------------------------------

    def test_default_dates_cover_current_month(self):
        wiz = self.Wizard.create({})
        today = date.today()
        first = today.replace(day=1)
        self.assertEqual(wiz.date_from, first)
        # date_to should be the last day of the current month — i.e.
        # >= today.
        self.assertGreaterEqual(wiz.date_to, today)
        self.assertEqual(wiz.date_to.month, today.month)

    # ------------------------------------------------------------------
    # _onchange_date_range
    # ------------------------------------------------------------------

    def test_onchange_today(self):
        wiz = self.Wizard.new({"date_range": "today"})
        wiz._onchange_date_range()
        today = date.today()
        self.assertEqual(wiz.date_from, today)
        self.assertEqual(wiz.date_to, today)

    def test_onchange_yesterday(self):
        wiz = self.Wizard.new({"date_range": "yesterday"})
        wiz._onchange_date_range()
        yesterday = date.today() - timedelta(days=1)
        self.assertEqual(wiz.date_from, yesterday)
        self.assertEqual(wiz.date_to, yesterday)

    def test_onchange_this_week(self):
        wiz = self.Wizard.new({"date_range": "this_week"})
        wiz._onchange_date_range()
        # 7-day span, monday-anchored.
        self.assertEqual(wiz.date_from.weekday(), 0)
        self.assertEqual((wiz.date_to - wiz.date_from).days, 6)

    def test_onchange_this_month(self):
        wiz = self.Wizard.new({"date_range": "this_month"})
        wiz._onchange_date_range()
        today = date.today()
        self.assertEqual(wiz.date_from.day, 1)
        self.assertEqual(wiz.date_from.month, today.month)
        self.assertEqual(wiz.date_to.month, today.month)

    def test_onchange_last_month(self):
        wiz = self.Wizard.new({"date_range": "last_month"})
        wiz._onchange_date_range()
        self.assertEqual(wiz.date_from.day, 1)
        # date_to is the last day of the previous month.
        self.assertNotEqual(wiz.date_to.month, date.today().month)

    def test_onchange_custom_does_not_touch_dates(self):
        wiz = self.Wizard.create({})
        wiz.date_from = date(2024, 1, 1)
        wiz.date_to = date(2024, 1, 31)
        wiz.date_range = "custom"
        wiz._onchange_date_range()
        self.assertEqual(wiz.date_from, date(2024, 1, 1))
        self.assertEqual(wiz.date_to, date(2024, 1, 31))

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    def test_check_dates_rejects_inverted_range(self):
        with self.assertRaises(UserError):
            self.Wizard.create({
                "date_range": "custom",
                "date_from": date(2026, 2, 1),
                "date_to": date(2026, 1, 1),
            })

    # ------------------------------------------------------------------
    # _get_options
    # ------------------------------------------------------------------

    def test_get_options_payload(self):
        wiz = self.Wizard.create({
            "date_range": "custom",
            "date_from": date(2026, 1, 1),
            "date_to": date(2026, 1, 31),
            "payment_state": "paid",
        })
        opts = wiz._get_options()
        self.assertEqual(opts["date_from"], "2026-01-01")
        self.assertEqual(opts["date_to"], "2026-01-31")
        self.assertEqual(opts["payment_state"], "paid")
        self.assertIn(self.env.company.id, opts["company_ids"])

    def test_get_options_falls_back_to_env_companies_when_empty(self):
        wiz = self.Wizard.create({})
        wiz.company_ids = [(5, 0, 0)]
        opts = wiz._get_options()
        self.assertEqual(opts["company_ids"], self.env.companies.ids)

    def test_get_options_includes_user_keys(self):
        """Salesperson and creator filters must round-trip into options."""
        seller = self.env["res.users"].create({
            "name": "Wizard Seller",
            "login": "wiz_seller_test@example.com",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        creator = self.env["res.users"].create({
            "name": "Wizard Creator",
            "login": "wiz_creator_test@example.com",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        wiz = self.Wizard.create({
            "invoice_user_ids": [(6, 0, [seller.id])],
            "create_user_ids": [(6, 0, [creator.id])],
        })
        opts = wiz._get_options()
        self.assertEqual(opts["invoice_user_ids"], [seller.id])
        self.assertEqual(opts["create_user_ids"], [creator.id])

    def test_get_options_user_keys_default_empty(self):
        wiz = self.Wizard.create({})
        opts = wiz._get_options()
        self.assertEqual(opts["invoice_user_ids"], [])
        self.assertEqual(opts["create_user_ids"], [])

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def test_action_view_dashboard(self):
        wiz = self.Wizard.create({})
        action = wiz.action_view_dashboard()
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "custom_billing_report.dashboard")
        self.assertIn("options", action["params"])

    def test_action_print_pdf(self):
        wiz = self.Wizard.create({})
        action = wiz.action_print_pdf()
        self.assertEqual(action["type"], "ir.actions.report")
        self.assertEqual(action["report_type"], "qweb-pdf")

    def test_action_print_xlsx_returns_url(self):
        wiz = self.Wizard.create({})
        action = wiz.action_print_xlsx()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertIn("/custom_billing_report/xlsx", action["url"])
        self.assertIn("options=", action["url"])
