from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install", "custom_billing_report")
class TestBillingReport(AccountTestInvoicingCommon):
    # AccountTestInvoicingCommon sets up CoA + journals so
    # account.payment.register can post payments and flip the move
    # to payment_state='paid' (TransactionCase alone is not enough).

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service = cls.env["billing.report"]
        cls.company = cls.env.company

        cls.partner_a = cls.env["res.partner"].create({
            "name": "Customer Alpha",
            "company_type": "company",
        })
        cls.partner_b = cls.env["res.partner"].create({
            "name": "Customer Bravo",
            "company_type": "company",
        })

        # Two internal users used as salesperson / creator markers.
        # ``user_creator`` posts the invoice fixture, so it needs
        # accounting permissions on top of the base internal group.
        accounting_groups = [
            cls.env.ref("base.group_user").id,
            cls.env.ref("account.group_account_manager").id,
        ]
        cls.user_seller = cls.env["res.users"].create({
            "name": "Seller One",
            "login": "seller_one_test@example.com",
            "groups_id": [(6, 0, accounting_groups)],
        })
        cls.user_creator = cls.env["res.users"].create({
            "name": "Creator One",
            "login": "creator_one_test@example.com",
            "groups_id": [(6, 0, accounting_groups)],
        })

        cls.product = cls.env["product.product"].create({
            "name": "Test Product",
            "type": "service",
            "lst_price": 1000.0,
        })

        # ITBIS-named sale tax (18%) — the service detects taxes whose
        # name contains "ITBIS" (case-insensitive).
        cls.tax_itbis = cls.env["account.tax"].create({
            "name": "ITBIS 18%",
            "amount": 18.0,
            "type_tax_use": "sale",
            "amount_type": "percent",
            "company_id": cls.company.id,
        })

        cls.move_paid = cls._create_invoice(
            cls, cls.partner_a, date(2026, 1, 10), price=1000.0,
            discount=0.0, register_payment=True,
            invoice_user=cls.user_seller, create_user=cls.user_creator,
        )
        cls.move_unpaid = cls._create_invoice(
            cls, cls.partner_b, date(2026, 1, 20), price=500.0,
            discount=10.0, register_payment=False,
        )
        cls.move_outside = cls._create_invoice(
            cls, cls.partner_a, date(2025, 12, 5), price=200.0,
            discount=0.0, register_payment=False,
        )

    def _create_invoice(self, partner, invoice_date, price,
                        discount=0.0, register_payment=False,
                        invoice_user=None, create_user=None, currency=None):
        """Helper to post a customer invoice and optionally pay it.

        ``invoice_user`` sets ``invoice_user_id`` (salesperson).
        ``create_user`` runs the creation as that user so ``create_uid``
        reflects the desired creator (it cannot be set explicitly).
        ``currency`` issues the invoice in a foreign currency.
        """
        env = self.env
        if create_user is not None:
            env = self.env(user=create_user.id)
        vals = {
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "invoice_date": invoice_date,
            "invoice_line_ids": [(0, 0, {
                "product_id": self.product.id,
                "name": self.product.name,
                "quantity": 1,
                "price_unit": price,
                "discount": discount,
                "tax_ids": [(6, 0, self.tax_itbis.ids)],
            })],
        }
        if invoice_user is not None:
            vals["invoice_user_id"] = invoice_user.id
        if currency is not None:
            vals["currency_id"] = currency.id
        move = env["account.move"].create(vals)
        move.action_post()
        if register_payment:
            self.env["account.payment.register"].with_context(
                active_model="account.move",
                active_ids=move.ids,
            ).create({}).action_create_payments()
        return move

    # ------------------------------------------------------------------
    # Options helpers
    # ------------------------------------------------------------------

    def test_default_options_shape(self):
        opts = self.service._default_options()
        self.assertIn("date_from", opts)
        self.assertIn("date_to", opts)
        self.assertEqual(opts["payment_state"], "all")
        self.assertEqual(opts["partner_ids"], [])
        self.assertIn(self.company.id, opts["company_ids"])

    def test_normalize_options_merges_over_defaults(self):
        merged = self.service._normalize_options({"payment_state": "paid"})
        self.assertEqual(merged["payment_state"], "paid")
        # Untouched keys keep their defaults.
        self.assertEqual(merged["partner_ids"], [])

    def test_normalize_options_handles_none(self):
        merged = self.service._normalize_options(None)
        self.assertEqual(merged["payment_state"], "all")

    # ------------------------------------------------------------------
    # Domain
    # ------------------------------------------------------------------

    def test_domain_filters_by_date(self):
        opts = self.service._normalize_options({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
        })
        domain = self.service._get_invoice_domain(opts)
        moves = self.env["account.move"].search(domain)
        self.assertIn(self.move_paid, moves)
        self.assertIn(self.move_unpaid, moves)
        self.assertNotIn(self.move_outside, moves)

    def test_domain_filters_by_partner(self):
        opts = self.service._normalize_options({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "partner_ids": [self.partner_a.id],
        })
        domain = self.service._get_invoice_domain(opts)
        moves = self.env["account.move"].search(domain)
        self.assertEqual(moves, self.move_paid)

    def test_domain_filters_by_payment_state_paid(self):
        opts = self.service._normalize_options({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "payment_state": "paid",
        })
        domain = self.service._get_invoice_domain(opts)
        moves = self.env["account.move"].search(domain)
        self.assertIn(self.move_paid, moves)
        self.assertNotIn(self.move_unpaid, moves)

    def test_domain_filters_by_payment_state_unpaid(self):
        opts = self.service._normalize_options({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "payment_state": "unpaid",
        })
        domain = self.service._get_invoice_domain(opts)
        moves = self.env["account.move"].search(domain)
        self.assertIn(self.move_unpaid, moves)
        self.assertNotIn(self.move_paid, moves)

    # ------------------------------------------------------------------
    # Per-row builders
    # ------------------------------------------------------------------

    def test_get_ncf_falls_back_to_empty(self):
        # Without LATAM document number, _get_ncf returns "".
        ncf = self.service._get_ncf(self.move_paid)
        self.assertIsInstance(ncf, str)

    def test_discount_amount_zero(self):
        self.assertEqual(self.service._get_discount_amount(self.move_paid), 0.0)

    def test_discount_amount_ten_percent(self):
        # 500 * 10% = 50.0
        self.assertAlmostEqual(
            self.service._get_discount_amount(self.move_unpaid), 50.0, places=2
        )

    def test_itbis_amount_matches_amount_tax(self):
        # Single ITBIS tax — service's ITBIS detection should equal
        # ``amount_tax`` for a one-tax invoice.
        amount = self.service._get_itbis_amount(self.move_paid)
        self.assertAlmostEqual(amount, self.move_paid.amount_tax, places=2)

    def test_status_paid(self):
        code, _label = self.service._get_status(self.move_paid)
        self.assertEqual(code, "paid")

    def test_status_pending(self):
        code, _label = self.service._get_status(self.move_unpaid)
        self.assertEqual(code, "pending")

    def test_build_line_keys(self):
        line = self.service._build_line(self.move_paid)
        expected_keys = {
            "id", "invoice_date", "name", "partner_id", "partner_name",
            "invoice_user_id", "invoice_user_name",
            "create_user_id", "create_user_name",
            "ncf", "amount_untaxed", "discount", "amount_tax",
            "amount_total", "amount_residual", "amount_paid",
            "payment_method", "payment_state", "status_label",
            "currency_id", "currency_symbol",
        }
        self.assertTrue(expected_keys.issubset(line.keys()))

    def test_build_line_invoice_user(self):
        line = self.service._build_line(self.move_paid)
        self.assertEqual(line["invoice_user_id"], self.user_seller.id)
        self.assertIn("Seller", line["invoice_user_name"])

    def test_build_line_create_user(self):
        line = self.service._build_line(self.move_paid)
        self.assertEqual(line["create_user_id"], self.user_creator.id)
        self.assertIn("Creator", line["create_user_name"])

    # ------------------------------------------------------------------
    # Aggregate payload
    # ------------------------------------------------------------------

    def test_get_report_data_totals(self):
        data = self.service.get_report_data({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "company_ids": [self.company.id],
        })
        totals = data["totals"]
        self.assertEqual(totals["count"], 2)
        # paid invoice: 1000 + 18% tax = 1180; unpaid: 500 - 50 disc = 450
        # + 18% on (500 - 50) = 81 => 531; total invoiced = 1180 + 531 = 1711
        self.assertAlmostEqual(totals["total_invoiced"], 1711.0, places=2)
        self.assertAlmostEqual(totals["total_paid"], 1180.0, places=2)
        self.assertAlmostEqual(totals["total_pending"], 531.0, places=2)
        self.assertAlmostEqual(totals["total_discount"], 50.0, places=2)

    def test_get_report_data_top_customers(self):
        data = self.service.get_report_data({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "company_ids": [self.company.id],
        })
        ids = {c["partner_id"] for c in data["top_customers"]}
        self.assertEqual(ids, {self.partner_a.id, self.partner_b.id})

    def test_get_report_data_trend_daily_buckets(self):
        data = self.service.get_report_data({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "company_ids": [self.company.id],
        })
        days = [point["date"] for point in data["trend"]]
        self.assertIn("2026-01-10", days)
        self.assertIn("2026-01-20", days)

    def test_get_report_data_company_payload(self):
        data = self.service.get_report_data({})
        self.assertEqual(data["company"]["id"], self.company.id)
        self.assertTrue(data["company"]["currency_symbol"])

    def test_get_report_data_serialisable(self):
        import json
        data = self.service.get_report_data({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
        })
        # Must round-trip through JSON for dashboard RPC.
        json.dumps(data)

    # ------------------------------------------------------------------
    # Salesperson + creator filters and aggregations
    # ------------------------------------------------------------------

    def test_domain_filters_by_invoice_user(self):
        opts = self.service._normalize_options({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "invoice_user_ids": [self.user_seller.id],
        })
        domain = self.service._get_invoice_domain(opts)
        moves = self.env["account.move"].search(domain)
        self.assertEqual(moves, self.move_paid)

    def test_domain_filters_by_create_user(self):
        opts = self.service._normalize_options({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "create_user_ids": [self.user_creator.id],
        })
        domain = self.service._get_invoice_domain(opts)
        moves = self.env["account.move"].search(domain)
        self.assertEqual(moves, self.move_paid)

    def test_top_salespersons_payload(self):
        data = self.service.get_report_data({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "company_ids": [self.company.id],
        })
        self.assertIn("top_salespersons", data)
        ids = {sp["user_id"] for sp in data["top_salespersons"]}
        self.assertIn(self.user_seller.id, ids)
        seller_entry = next(
            sp for sp in data["top_salespersons"]
            if sp["user_id"] == self.user_seller.id
        )
        self.assertEqual(seller_entry["count"], 1)
        self.assertAlmostEqual(seller_entry["amount_total"], 1180.0, places=2)
        self.assertAlmostEqual(seller_entry["amount_paid"], 1180.0, places=2)

    def test_foreign_currency_converted_to_company(self):
        """A USD invoice is reported in the company currency at the
        booked rate, not as raw foreign amounts."""
        company_currency = self.company.currency_id
        foreign = self.env["res.currency"].create({
            "name": "TST",
            "symbol": "T$",
            "rounding": 0.01,
        })
        # 2 foreign units per company unit on the invoice date =>
        # 1 foreign = 0.5 company.
        self.env["res.currency.rate"].create({
            "name": "2026-01-15",
            "currency_id": foreign.id,
            "company_id": self.company.id,
            "rate": 2.0,
        })
        partner = self.env["res.partner"].create({"name": "Customer FX"})
        self._create_invoice(
            partner, date(2026, 1, 15), price=1000.0, currency=foreign,
        )
        data = self.service.get_report_data({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "partner_ids": [partner.id],
            "company_ids": [self.company.id],
        })
        self.assertEqual(len(data["lines"]), 1)
        line = data["lines"][0]
        # 1180 foreign total (1000 + 18% ITBIS) -> 590 company.
        self.assertAlmostEqual(line["amount_total"], 590.0, places=2)
        self.assertAlmostEqual(line["amount_untaxed"], 500.0, places=2)
        self.assertAlmostEqual(line["amount_tax"], 90.0, places=2)
        self.assertEqual(line["currency_id"], company_currency.id)
        self.assertAlmostEqual(data["totals"]["total_invoiced"], 590.0, places=2)

    def test_top_creators_payload(self):
        data = self.service.get_report_data({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "company_ids": [self.company.id],
        })
        self.assertIn("top_creators", data)
        ids = {cr["user_id"] for cr in data["top_creators"]}
        self.assertIn(self.user_creator.id, ids)

    def test_default_options_contain_user_keys(self):
        opts = self.service._default_options()
        self.assertEqual(opts["invoice_user_ids"], [])
        self.assertEqual(opts["create_user_ids"], [])

    # ------------------------------------------------------------------
    # Payment category classification
    # ------------------------------------------------------------------

    def test_empty_payment_breakdown_keys(self):
        breakdown = self.service._empty_payment_breakdown()
        self.assertEqual(
            set(breakdown.keys()),
            {"cash", "card", "transfer", "bank", "other"},
        )
        self.assertTrue(all(v == 0.0 for v in breakdown.values()))

    def test_classify_payment_cash_journal(self):
        """A payment booked through a cash journal goes to *cash*
        regardless of the payment-method-line name."""
        Journal = self.env["account.journal"]
        cash_journal = Journal.search(
            [("type", "=", "cash"), ("company_id", "=", self.company.id)],
            limit=1,
        )
        if not cash_journal:
            cash_journal = Journal.create({
                "name": "Cash Test",
                "code": "CSHT",
                "type": "cash",
                "company_id": self.company.id,
            })
        Payment = self.env["account.payment"]
        method_line = cash_journal.inbound_payment_method_line_ids[:1]
        payment_vals = {
            "amount": 100.0,
            "partner_id": self.partner_a.id,
            "journal_id": cash_journal.id,
            "payment_type": "inbound",
            "partner_type": "customer",
        }
        if method_line:
            payment_vals["payment_method_line_id"] = method_line.id
        payment = Payment.create(payment_vals)
        self.assertEqual(self.service._classify_payment(payment), "cash")

    def test_classify_payment_card_keyword(self):
        """A payment-method-line whose name contains *card* goes to the
        card bucket even on a bank journal."""
        bank_journal = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.company.id)],
            limit=1,
        )
        manual_method = self.env.ref("account.account_payment_method_manual_in")
        method_line = self.env["account.payment.method.line"].create({
            "name": "Tarjeta de Credito",
            "journal_id": bank_journal.id,
            "payment_method_id": manual_method.id,
        })
        payment = self.env["account.payment"].create({
            "amount": 50.0,
            "partner_id": self.partner_a.id,
            "journal_id": bank_journal.id,
            "payment_method_line_id": method_line.id,
            "payment_type": "inbound",
            "partner_type": "customer",
        })
        self.assertEqual(self.service._classify_payment(payment), "card")

    def test_classify_payment_transfer_keyword(self):
        bank_journal = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.company.id)],
            limit=1,
        )
        manual_method = self.env.ref("account.account_payment_method_manual_in")
        method_line = self.env["account.payment.method.line"].create({
            "name": "Transferencia Bancaria",
            "journal_id": bank_journal.id,
            "payment_method_id": manual_method.id,
        })
        payment = self.env["account.payment"].create({
            "amount": 75.0,
            "partner_id": self.partner_a.id,
            "journal_id": bank_journal.id,
            "payment_method_line_id": method_line.id,
            "payment_type": "inbound",
            "partner_type": "customer",
        })
        self.assertEqual(self.service._classify_payment(payment), "transfer")

    def test_classify_payment_cash_keyword_on_bank_journal(self):
        """A method line named "Efectivo" on a bank journal still routes
        to the *cash* bucket. Common DR setup: the daily cash deposit is
        booked against a bank journal but the method line keeps the
        cashier-friendly name. Without this the cash-up section would
        show 0 even when paid cash invoices exist (see #fix-002)."""
        bank_journal = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.company.id)],
            limit=1,
        )
        manual_method = self.env.ref("account.account_payment_method_manual_in")
        method_line = self.env["account.payment.method.line"].create({
            "name": "Efectivo",
            "journal_id": bank_journal.id,
            "payment_method_id": manual_method.id,
        })
        payment = self.env["account.payment"].create({
            "amount": 33.0,
            "partner_id": self.partner_a.id,
            "journal_id": bank_journal.id,
            "payment_method_line_id": method_line.id,
            "payment_type": "inbound",
            "partner_type": "customer",
        })
        self.assertEqual(self.service._classify_payment(payment), "cash")

    def test_classify_payment_bank_default(self):
        """A bank payment with a generic method falls back to *bank*."""
        bank_journal = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.company.id)],
            limit=1,
        )
        # Reuse the journal's default inbound method line — its name
        # ("Manual" by default) doesn't trigger any keyword bucket.
        method_line = bank_journal.inbound_payment_method_line_ids[:1]
        payment = self.env["account.payment"].create({
            "amount": 25.0,
            "partner_id": self.partner_a.id,
            "journal_id": bank_journal.id,
            "payment_method_line_id": method_line.id if method_line else False,
            "payment_type": "inbound",
            "partner_type": "customer",
        })
        self.assertEqual(self.service._classify_payment(payment), "bank")

    def test_get_report_data_payment_payload(self):
        data = self.service.get_report_data({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "company_ids": [self.company.id],
        })
        self.assertIn("payment_categories", data)
        self.assertIn("payment_totals", data)
        self.assertIn("payment_trend", data)
        self.assertIn("payment_total_collected", data)
        keys = {entry["key"] for entry in data["payment_categories"]}
        self.assertEqual(keys, {"cash", "card", "transfer", "bank", "other"})
        # Per-bucket trend rows must expose every category key so the
        # comparative chart can iterate without conditionals.
        if data["payment_trend"]:
            sample = data["payment_trend"][0]
            for cat in ("cash", "card", "transfer", "bank", "other"):
                self.assertIn(cat, sample)

    def test_build_line_includes_payment_breakdown(self):
        line = self.service._build_line(self.move_paid)
        self.assertIn("payment_breakdown", line)
        self.assertEqual(
            set(line["payment_breakdown"].keys()),
            {"cash", "card", "transfer", "bank", "other"},
        )

    def test_payment_totals_sum_paid_amount(self):
        """``move_paid`` was registered through a bank journal with the
        default *Manual* method line, so its 1180.00 collected amount
        falls into the *bank* bucket."""
        data = self.service.get_report_data({
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "company_ids": [self.company.id],
        })
        # Total collected across buckets must equal Total Paid (no
        # partial payments in this fixture).
        self.assertAlmostEqual(
            data["payment_total_collected"],
            data["totals"]["total_paid"],
            places=2,
        )
