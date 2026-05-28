# -*- coding: utf-8 -*-
"""Billing Report — data service.

This module exposes :class:`BillingReport`, an ``AbstractModel`` that
centralises the logic to build the dataset consumed by:

* the OWL dashboard (``custom_billing_report.dashboard`` client action),
* the QWeb PDF report,
* the Excel export controller.

Keeping the data layer in a single place guarantees that the three
output channels stay in sync (totals, per-line values, formatting).

The service operates on ``account.move`` records and works on plain
Odoo (no localization required). Optional Dominican Republic columns
(``l10n_do_fiscal_number`` / ``l10n_latam_document_number`` /
``l10n_latam_document_type_id``) are picked up automatically when the
relevant modules are installed.
"""

from datetime import date, datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Move types considered as customer billing for this report.
CUSTOMER_MOVE_TYPES = ("out_invoice", "out_refund")

#: ``payment_state`` values that we treat as "fully paid" for KPI purposes.
PAID_STATES = ("paid", "in_payment", "reversed")

#: ``payment_state`` values that we treat as "still owed".
UNPAID_STATES = ("not_paid", "partial")

#: Payment categories surfaced as separate totals/charts so the
#: cashier can run the daily cash-up ("cuadre diario"). Order matters
#: — used for chart series order and PDF column order.
PAYMENT_CATEGORIES = ("cash", "card", "transfer", "bank", "other")

#: Keyword buckets used to classify a payment into one of the
#: categories above. Matching is case-insensitive against both the
#: payment-method-line name and the underlying payment method code.
#: Order is important — the first matching bucket wins, so put the
#: most specific keywords (cash / card / transfer) before the catch-
#: alls. ``cash`` is listed first so that a method/journal named
#: "Efectivo" (or any cash synonym) gets routed to the cash bucket
#: even when the underlying journal is of type ``bank`` — common in
#: setups where the daily cash deposit is booked against a bank
#: journal instead of a dedicated cash journal.
PAYMENT_CATEGORY_KEYWORDS = {
    "cash": (
        "cash", "efectivo", "contado", "caja",
    ),
    "card": (
        "card", "tarjeta", "credit", "debit", "debito", "credito", "visa",
        "mastercard", "amex", "pos",
    ),
    "transfer": (
        "transfer", "transferencia", "wire", "ach", "swift", "sinpe",
    ),
}


class BillingReport(models.AbstractModel):
    """Service model — builds the billing dataset from filter options.

    The single public entry-point is :meth:`get_report_data` which
    receives an ``options`` dict (see :meth:`_default_options` for the
    accepted keys) and returns a serialisable payload usable by both
    server-side renderers (PDF / XLSX) and the OWL dashboard.
    """

    _name = "billing.report"
    _description = "Billing Report Service"

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    @api.model
    def _check_report_access(self):
        """Restrict the report to accounting administrators.

        The dashboard, wizard and PDF/XLSX entry-points all funnel
        through :meth:`get_report_data`, so guarding here covers every
        consumer (including direct RPC calls).
        """
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(_("You are not allowed to access the Billing Report."))

    # ------------------------------------------------------------------
    # Options helpers
    # ------------------------------------------------------------------

    @api.model
    def _default_options(self):
        """Return the canonical option dict with sensible defaults.

        Used as a fallback when the caller (e.g. the dashboard) opens
        the view without going through the wizard.
        """
        today = fields.Date.context_today(self)
        first_of_month = today.replace(day=1)
        next_month = (first_of_month + timedelta(days=32)).replace(day=1)
        last_of_month = next_month - timedelta(days=1)
        return {
            "date_from": fields.Date.to_string(first_of_month),
            "date_to": fields.Date.to_string(last_of_month),
            "partner_ids": [],
            "payment_state": "all",
            "payment_method_ids": [],
            "company_ids": self.env.companies.ids,
            "l10n_latam_document_type_ids": [],
            "invoice_user_ids": [],
            "create_user_ids": [],
            "trend_granularity": "auto",
        }

    @api.model
    def _normalize_options(self, options):
        """Merge incoming ``options`` over the defaults.

        Keeps every consumer (wizard, dashboard, controllers) tolerant
        to partial payloads — missing keys fall back to defaults.
        """
        merged = self._default_options()
        merged.update(options or {})
        return merged

    # ------------------------------------------------------------------
    # Domain
    # ------------------------------------------------------------------

    @api.model
    def _get_invoice_domain(self, options):
        """Return the search domain on ``account.move`` for ``options``."""
        domain = [
            ("move_type", "in", CUSTOMER_MOVE_TYPES),
            ("state", "=", "posted"),
        ]
        if options.get("date_from"):
            domain.append(("invoice_date", ">=", options["date_from"]))
        if options.get("date_to"):
            domain.append(("invoice_date", "<=", options["date_to"]))
        if options.get("partner_ids"):
            domain.append(("partner_id", "in", options["partner_ids"]))
        if options.get("company_ids"):
            domain.append(("company_id", "in", options["company_ids"]))
        if options.get("invoice_user_ids"):
            domain.append(("invoice_user_id", "in", options["invoice_user_ids"]))
        if options.get("create_user_ids"):
            domain.append(("create_uid", "in", options["create_user_ids"]))

        payment_state = options.get("payment_state") or "all"
        if payment_state == "paid":
            domain.append(("payment_state", "in", PAID_STATES))
        elif payment_state == "unpaid":
            domain.append(("payment_state", "in", UNPAID_STATES))

        # NCF / LATAM document type filter — only applied when the field
        # actually exists on ``account.move`` (l10n_latam_invoice_document
        # installed).
        latam_types = options.get("l10n_latam_document_type_ids")
        if latam_types and "l10n_latam_document_type_id" in self.env["account.move"]._fields:
            domain.append(("l10n_latam_document_type_id", "in", latam_types))

        # Payment method filter — implemented as a sub-search through
        # the reconciled payments. Done at the move level via
        # ``matched_payment_ids`` for clean SQL.
        method_ids = options.get("payment_method_ids")
        if method_ids and "matched_payment_ids" in self.env["account.move"]._fields:
            domain.append(
                ("matched_payment_ids.payment_method_line_id", "in", method_ids)
            )
        return domain

    # ------------------------------------------------------------------
    # Per-move row builder
    # ------------------------------------------------------------------

    @api.model
    def _get_ncf(self, move):
        """Return the NCF (or LATAM document number) for ``move``.

        Falls back gracefully when no localization field is present.
        """
        if "l10n_do_fiscal_number" in move._fields and move.l10n_do_fiscal_number:
            return move.l10n_do_fiscal_number
        if "l10n_latam_document_number" in move._fields and move.l10n_latam_document_number:
            return move.l10n_latam_document_number
        return ""

    # ------------------------------------------------------------------
    # Currency conversion
    # ------------------------------------------------------------------
    #
    # Invoices may be issued in a foreign currency (e.g. USD) while the
    # company keeps its books in DOP. Every monetary figure surfaced by
    # the report is normalised to the company currency *at the rate that
    # was booked on the document* ("la tasa aplicada en ese momento"),
    # so totals across mixed-currency invoices add up correctly instead
    # of summing raw foreign amounts.

    @api.model
    def _conversion_rate(self, from_currency, company, conv_date):
        """Return the company-per-``from_currency`` rate on ``conv_date``.

        ``1.0`` when no conversion is needed (missing currency or the
        document is already in the company currency). Used as the
        fallback for documents whose own amounts cannot yield a booked
        rate (e.g. a zero-total move) and for POS rows.
        """
        company_currency = company.currency_id
        if not from_currency or from_currency == company_currency:
            return 1.0
        conv_date = conv_date or fields.Date.context_today(self)
        return from_currency._convert(1.0, company_currency, company, conv_date)

    @api.model
    def _move_rate(self, move):
        """Return the company-per-document-currency rate booked on ``move``.

        Derived from the move's own signed amounts so it reflects the FX
        rate captured at posting time rather than today's rate table.
        Falls back to the dated rate when the move carries no total.
        """
        if move.currency_id == move.company_currency_id:
            return 1.0
        doc_amount = move.amount_total_in_currency_signed
        comp_amount = move.amount_total_signed
        if doc_amount and comp_amount:
            return abs(comp_amount / doc_amount)
        return self._conversion_rate(
            move.currency_id, move.company_id, move.invoice_date
        )

    @api.model
    def _get_discount_amount(self, move):
        """Sum of line discounts (in the document currency) for ``move``.

        Computed from ``invoice_line_ids`` so it ignores tax / payment
        terms / section lines. Callers convert the result to the company
        currency at the move's booked rate.
        """
        sign = -1 if move.move_type == "out_refund" else 1
        total = 0.0
        for line in move.invoice_line_ids:
            if line.display_type and line.display_type != "product":
                continue
            gross = line.price_unit * line.quantity
            total += gross * (line.discount or 0.0) / 100.0
        return sign * total

    @api.model
    def _get_itbis_amount(self, move, rate=1.0):
        """Return the ITBIS portion of ``amount_tax`` in company currency.

        We consider a tax to be ITBIS when its name contains the
        substring "ITBIS" (case-insensitive). When no such tax is
        found we fall back to the full ``amount_tax`` so the column is
        never empty on non-RD installs.

        ``rate`` is the move's booked company-per-document rate; it is
        only applied to the ``amount_tax`` fallback, which is expressed
        in the document currency. The ITBIS tax lines are read from
        ``balance`` which is already in the company currency.
        """
        itbis = 0.0
        found = False
        for tax_line in move.line_ids:
            tax = tax_line.tax_line_id
            if not tax:
                continue
            if "ITBIS" in (tax.name or "").upper():
                # ``balance`` is signed by debit/credit; we want the
                # positive tax amount in company currency for the move.
                itbis += abs(tax_line.balance)
                found = True
        if not found:
            fallback = move.company_currency_id.round(move.amount_tax * rate)
            return -fallback if move.move_type == "out_refund" else fallback
        # Match the sign convention of refunds.
        return -itbis if move.move_type == "out_refund" else itbis

    @api.model
    def _pos_installed(self):
        """Soft check — ``True`` when ``point_of_sale`` is installed.

        The module does not declare POS as a dependency: every POS code
        path is gated by this check so the report keeps working on
        installs without POS.
        """
        return "pos.order" in self.env

    @api.model
    def _move_pos_orders(self, move):
        """Return the POS orders linked to ``move`` (empty when POS off).

        Uses ``sudo()`` because the report is gated by
        ``account.group_account_manager`` and accountants typically
        lack POS read access — without elevation, the o2m read raises
        ``AccessError`` on installs with ``point_of_sale``.
        """
        if not self._pos_installed():
            return None
        if "pos_order_ids" not in move._fields:
            return None
        return move.sudo().pos_order_ids

    @api.model
    def _get_payment_method_label(self, move):
        """Comma-separated list of payment-method names on ``move``.

        When the move was generated from a POS order we read names from
        ``pos.payment`` instead — POS does not always create reconciled
        ``account.payment`` records, so ``matched_payment_ids`` would
        miss the cashier's collection.
        """
        pos_orders = self._move_pos_orders(move)
        if pos_orders:
            names = pos_orders.payment_ids.mapped("payment_method_id.name")
            return ", ".join(n for n in names if n)
        if "matched_payment_ids" not in move._fields:
            return ""
        payments = move.matched_payment_ids
        names = payments.mapped("payment_method_line_id.name")
        return ", ".join(n for n in names if n)

    @api.model
    def _classify_payment(self, payment):
        """Return the category bucket (``cash``/``card``/``transfer``/
        ``bank``/``other``) for an ``account.payment`` record.

        Resolution order:

        1. ``journal.type == 'cash'`` always wins — physical cash
           handled in a cash journal goes to the *cash* bucket no
           matter how the payment method is named.
        2. ``payment_method_line.name`` and the underlying payment
           method ``code`` are scanned against the keyword buckets in
           :data:`PAYMENT_CATEGORY_KEYWORDS` (card, transfer).
        3. ``journal.type == 'bank'`` defaults to *bank*.
        4. Anything else falls back to *other*.
        """
        journal = payment.journal_id
        if journal and journal.type == "cash":
            return "cash"

        method_line = payment.payment_method_line_id
        haystack_parts = []
        if method_line:
            haystack_parts.append((method_line.name or "").lower())
            method = method_line.payment_method_id
            if method:
                haystack_parts.append((method.code or "").lower())
                haystack_parts.append((method.name or "").lower())
        haystack = " ".join(p for p in haystack_parts if p)

        if haystack:
            for category, keywords in PAYMENT_CATEGORY_KEYWORDS.items():
                if any(kw in haystack for kw in keywords):
                    return category

        if journal and journal.type == "bank":
            return "bank"
        return "other"

    @api.model
    def _classify_pos_payment(self, pos_payment):
        """Same buckets as :meth:`_classify_payment` but for ``pos.payment``.

        ``pos.payment`` carries a ``payment_method_id`` (``pos.payment.method``)
        instead of an ``account.payment.method.line``. Resolution order
        mirrors the ``account.payment`` classifier:

        1. ``method.is_cash_count`` (cash POS method) → *cash*.
        2. Keyword scan on method name + journal name/code.
        3. ``journal.type == 'bank'`` → *bank*.
        4. Fallback → *other*.
        """
        method = pos_payment.payment_method_id
        journal = method.journal_id if method else None
        if method and method.is_cash_count:
            return "cash"
        haystack_parts = []
        if method:
            haystack_parts.append((method.name or "").lower())
        if journal:
            haystack_parts.append((journal.name or "").lower())
            haystack_parts.append((journal.code or "").lower())
        haystack = " ".join(p for p in haystack_parts if p)
        if haystack:
            for category, keywords in PAYMENT_CATEGORY_KEYWORDS.items():
                if any(kw in haystack for kw in keywords):
                    return category
        if journal and journal.type == "bank":
            return "bank"
        return "other"

    @api.model
    def _empty_payment_breakdown(self):
        """Return a fresh ``{category: 0.0}`` dict in canonical order."""
        return {cat: 0.0 for cat in PAYMENT_CATEGORIES}

    @api.model
    def _get_payment_breakdown(self, move):
        """Sum each reconciled payment of ``move`` into its category.

        Refunds are signed negative so they reduce the cash-up total
        for the corresponding bucket — which is what the daily arqueo
        expects (a returned card sale shrinks the *card* total).

        Moves issued from POS read their breakdown from the linked
        ``pos.payment`` records instead: POS sessions reconcile through
        statement lines and may not produce ``account.payment`` rows,
        so ``matched_payment_ids`` would under-report cash collections.
        """
        breakdown = self._empty_payment_breakdown()
        sign = -1 if move.move_type == "out_refund" else 1
        pos_orders = self._move_pos_orders(move)
        if pos_orders:
            for payment in pos_orders.payment_ids:
                category = self._classify_pos_payment(payment)
                breakdown[category] += sign * self._pos_payment_company_amount(payment)
            return breakdown
        if "matched_payment_ids" not in move._fields:
            return breakdown
        for payment in move.matched_payment_ids:
            category = self._classify_payment(payment)
            breakdown[category] += sign * self._payment_company_amount(payment)
        return breakdown

    @api.model
    def _payment_company_amount(self, payment):
        """Return an ``account.payment`` amount in the company currency.

        Prefers the move's stored company-currency figure (booked rate);
        falls back to a dated conversion of ``amount`` when the field is
        unavailable.
        """
        if "amount_company_currency_signed" in payment._fields:
            return abs(payment.amount_company_currency_signed)
        rate = self._conversion_rate(
            payment.currency_id, payment.company_id, payment.date
        )
        return payment.company_id.currency_id.round((payment.amount or 0.0) * rate)

    @api.model
    def _pos_payment_company_amount(self, pos_payment):
        """Return a ``pos.payment`` amount in the company currency.

        POS payments are expressed in the order currency; we convert at
        the payment date so a foreign-currency POS sale lands in the
        cash-up at the company-currency value.
        """
        order = pos_payment.pos_order_id
        company = order.company_id if order else self.env.company
        currency = order.currency_id if order else company.currency_id
        rate = self._conversion_rate(
            currency, company, pos_payment.payment_date or (order and order.date_order)
        )
        return company.currency_id.round((pos_payment.amount or 0.0) * rate)

    @api.model
    def _get_status(self, move):
        """Return ``(code, label)`` for the human-readable payment state."""
        if move.payment_state in PAID_STATES:
            return "paid", _("Paid")
        return "pending", _("Pending Payment")

    @api.model
    def _build_line(self, move):
        """Render a single move into the report row dict.

        All monetary fields are normalised to the company currency at
        the move's booked exchange rate so that rows in different
        currencies (e.g. USD invoices) sum correctly with the rest.
        """
        status_code, status_label = self._get_status(move)
        company_currency = move.company_currency_id
        rate = self._move_rate(move)
        amount_untaxed = company_currency.round(move.amount_untaxed * rate)
        amount_total = company_currency.round(move.amount_total * rate)
        amount_residual = company_currency.round(move.amount_residual * rate)
        discount = company_currency.round(self._get_discount_amount(move) * rate)
        return {
            "id": move.id,
            "invoice_date": fields.Date.to_string(move.invoice_date) if move.invoice_date else "",
            "name": move.name or "",
            "partner_id": move.partner_id.id,
            "partner_name": move.partner_id.display_name or "",
            "invoice_user_id": move.invoice_user_id.id if move.invoice_user_id else False,
            "invoice_user_name": move.invoice_user_id.display_name or "",
            "create_user_id": move.create_uid.id if move.create_uid else False,
            "create_user_name": move.create_uid.display_name or "",
            "ncf": self._get_ncf(move),
            "amount_untaxed": amount_untaxed,
            "discount": discount,
            "amount_tax": self._get_itbis_amount(move, rate),
            "amount_total": amount_total,
            "amount_residual": amount_residual,
            "amount_paid": amount_total - amount_residual,
            "payment_method": self._get_payment_method_label(move),
            "payment_breakdown": self._get_payment_breakdown(move),
            "payment_state": status_code,
            "status_label": status_label,
            "currency_id": company_currency.id,
            "currency_symbol": company_currency.symbol or company_currency.name,
        }

    @api.model
    def _get_payment_category_labels(self):
        """Return ordered ``[(key, label), ...]`` for each category.

        Centralises the translation strings so PDF / XLSX / dashboard
        all surface the buckets with the same wording.
        """
        return [
            ("cash", _("Cash")),
            ("card", _("Card")),
            ("transfer", _("Transfer")),
            ("bank", _("Bank")),
            ("other", _("Other")),
        ]

    # ------------------------------------------------------------------
    # POS — soft-optional source
    # ------------------------------------------------------------------

    @api.model
    def _get_pos_order_domain(self, options):
        """Return the search domain on ``pos.order`` for ``options``.

        ``date_order`` is a ``Datetime``; we extend ``date_to`` to the
        end of the day so a "today" preset still includes orders booked
        in the afternoon.
        """
        domain = [("state", "in", ("paid", "done", "invoiced"))]
        if options.get("date_from"):
            domain.append(("date_order", ">=", options["date_from"]))
        if options.get("date_to"):
            domain.append(("date_order", "<=", "%s 23:59:59" % options["date_to"]))
        if options.get("partner_ids"):
            domain.append(("partner_id", "in", options["partner_ids"]))
        if options.get("company_ids"):
            domain.append(("company_id", "in", options["company_ids"]))
        # ``pos.order.user_id`` is the cashier — we treat it as both
        # salesperson and creator since POS does not split the two.
        if options.get("create_user_ids"):
            domain.append(("user_id", "in", options["create_user_ids"]))
        return domain

    @api.model
    def _get_pos_ncf(self, order):
        """Return the NCF for a POS order, when the DR localization is on."""
        if "l10n_do_fiscal_number" in order._fields and order.l10n_do_fiscal_number:
            return order.l10n_do_fiscal_number
        if "l10n_latam_document_number" in order._fields and order.l10n_latam_document_number:
            return order.l10n_latam_document_number
        return ""

    @api.model
    def _build_pos_line(self, order):
        """Render a non-invoiced POS order as a report row.

        Invoiced POS orders are already represented by their
        ``account.move``; we only emit a POS row for cash sales that
        never produced an invoice, otherwise revenue would be double-
        counted.
        """
        company = order.company_id or self.env.company
        company_currency = company.currency_id
        rate = self._conversion_rate(order.currency_id, company, order.date_order)
        breakdown = self._empty_payment_breakdown()
        for payment in order.payment_ids:
            breakdown[self._classify_pos_payment(payment)] += (
                self._pos_payment_company_amount(payment)
            )
        names = order.payment_ids.mapped("payment_method_id.name")
        payment_label = ", ".join(n for n in names if n)
        amount_paid = company_currency.round((order.amount_paid or 0.0) * rate)
        amount_total = company_currency.round((order.amount_total or 0.0) * rate)
        amount_residual = max(0.0, amount_total - amount_paid)
        is_paid = abs(amount_residual) < 0.005
        status_code = "paid" if is_paid else "pending"
        status_label = _("Paid") if is_paid else _("Pending Payment")
        invoice_date = ""
        if order.date_order:
            invoice_date = fields.Date.to_string(
                fields.Datetime.context_timestamp(self, order.date_order).date()
            )
        partner = order.partner_id
        cashier = order.user_id
        amount_tax = company_currency.round((order.amount_tax or 0.0) * rate)
        return {
            "id": order.id,
            "source": "pos",
            "invoice_date": invoice_date,
            "name": order.name or "",
            "partner_id": partner.id if partner else False,
            "partner_name": partner.display_name if partner else _("POS Walk-in"),
            "invoice_user_id": cashier.id if cashier else False,
            "invoice_user_name": cashier.display_name if cashier else "",
            "create_user_id": cashier.id if cashier else False,
            "create_user_name": cashier.display_name if cashier else "",
            "ncf": self._get_pos_ncf(order),
            "amount_untaxed": amount_total - amount_tax,
            "discount": 0.0,
            "amount_tax": amount_tax,
            "amount_total": amount_total,
            "amount_residual": amount_residual,
            "amount_paid": amount_paid,
            "payment_method": payment_label,
            "payment_breakdown": breakdown,
            "payment_state": status_code,
            "status_label": status_label,
            "currency_id": company_currency.id,
            "currency_symbol": company_currency.symbol or company_currency.name,
        }

    @api.model
    def _get_pos_lines(self, options):
        """Return POS rows for orders that are NOT linked to an invoice.

        Invoiced POS orders surface through the ``account.move`` flow
        and we already redirect their payment breakdown to the linked
        ``pos.payment`` records, so we exclude them here to avoid
        double-counting revenue.
        """
        if not self._pos_installed():
            return []
        domain = self._get_pos_order_domain(options)
        domain.append(("account_move", "=", False))
        # ``sudo()`` — accountants running this report do not have POS
        # access; elevation here keeps the soft-dep promise without
        # forcing every accountant into the POS group.
        orders = self.env["pos.order"].sudo().search(
            domain, order="date_order asc, name asc"
        )
        payment_state = options.get("payment_state") or "all"
        if payment_state == "unpaid":
            orders = orders.filtered(
                lambda o: (o.amount_total or 0.0) - (o.amount_paid or 0.0) > 0.005
            )
        elif payment_state == "paid":
            orders = orders.filtered(
                lambda o: abs((o.amount_total or 0.0) - (o.amount_paid or 0.0)) < 0.005
            )
        return [self._build_pos_line(o) for o in orders]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @api.model
    def _resolve_granularity(self, options):
        """Decide the bucketing for the trend chart.

        ``trend_granularity`` may be ``auto`` (default), ``day``,
        ``week`` or ``month``. ``auto`` picks based on the date range
        span: <=31d → day, <=120d → week, else month.
        """
        gran = (options or {}).get("trend_granularity") or "auto"
        if gran in ("day", "week", "month"):
            return gran
        date_from = options.get("date_from")
        date_to = options.get("date_to")
        if not (date_from and date_to):
            return "day"
        try:
            d_from = fields.Date.from_string(date_from)
            d_to = fields.Date.from_string(date_to)
        except (TypeError, ValueError):
            return "day"
        span = (d_to - d_from).days
        if span <= 31:
            return "day"
        if span <= 120:
            return "week"
        return "month"

    @api.model
    def _bucket_key(self, day_str, granularity):
        """Return the canonical bucket label for ``day_str``."""
        d = fields.Date.from_string(day_str)
        if granularity == "month":
            return d.replace(day=1).isoformat()
        if granularity == "week":
            # ISO week starting Monday — anchor on the Monday date.
            monday = d - timedelta(days=d.weekday())
            return monday.isoformat()
        return d.isoformat()

    @api.model
    def _build_trend(self, options, lines):
        granularity = self._resolve_granularity(options)
        buckets = {}
        for line in lines:
            day = line["invoice_date"]
            if not day:
                continue
            key = self._bucket_key(day, granularity)
            bucket = buckets.setdefault(key, {
                "invoiced": 0.0, "paid": 0.0, "pending": 0.0,
            })
            bucket["invoiced"] += line["amount_total"]
            bucket["paid"] += line["amount_paid"]
            bucket["pending"] += line["amount_residual"]
        trend = [
            {"date": k, **v} for k, v in sorted(buckets.items())
        ]
        return trend, granularity

    @api.model
    def get_report_data(self, options=None):
        """Return ``{options, lines, totals, ...}`` for the given filters.

        The returned dict is JSON-serialisable so it can be sent
        verbatim to the OWL dashboard via RPC.
        """
        self._check_report_access()
        options = self._normalize_options(options)
        domain = self._get_invoice_domain(options)
        moves = self.env["account.move"].search(
            domain, order="invoice_date asc, name asc"
        )

        lines = [self._build_line(m) for m in moves]

        # Soft POS source — non-invoiced orders only; invoiced POS
        # orders already appear via ``moves`` and their breakdown is
        # redirected to ``pos.payment`` in :meth:`_get_payment_breakdown`.
        pos_lines = self._get_pos_lines(options)
        if pos_lines:
            lines.extend(pos_lines)
            lines.sort(key=lambda l: (l.get("invoice_date") or "", l.get("name") or ""))

        totals = {
            "count": len(lines),
            "total_untaxed": sum(l["amount_untaxed"] for l in lines),
            "total_discount": sum(l["discount"] for l in lines),
            "total_tax": sum(l["amount_tax"] for l in lines),
            "total_invoiced": sum(l["amount_total"] for l in lines),
            "total_pending": sum(l["amount_residual"] for l in lines),
            "total_paid": sum(l["amount_paid"] for l in lines),
        }

        # Aggregations used by the dashboard charts (top customers,
        # paid vs pending breakdown). They are cheap and avoid a second
        # round-trip from the front-end.
        by_partner = {}
        for line in lines:
            key = line["partner_id"]
            if key not in by_partner:
                by_partner[key] = {
                    "partner_id": key,
                    "partner_name": line["partner_name"],
                    "amount_total": 0.0,
                    "amount_paid": 0.0,
                    "amount_pending": 0.0,
                }
            by_partner[key]["amount_total"] += line["amount_total"]
            by_partner[key]["amount_paid"] += line["amount_paid"]
            by_partner[key]["amount_pending"] += line["amount_residual"]

        top_customers = sorted(
            by_partner.values(),
            key=lambda d: d["amount_total"],
            reverse=True,
        )[:10]

        # Per-salesperson aggregation. Moves without an invoice_user_id
        # bucket into a "Sin asignar" group so totals always reconcile.
        by_user = {}
        for line in lines:
            key = line["invoice_user_id"] or 0
            if key not in by_user:
                by_user[key] = {
                    "user_id": line["invoice_user_id"] or False,
                    "user_name": line["invoice_user_name"] or _("Unassigned"),
                    "count": 0,
                    "amount_total": 0.0,
                    "amount_paid": 0.0,
                    "amount_pending": 0.0,
                }
            entry = by_user[key]
            entry["count"] += 1
            entry["amount_total"] += line["amount_total"]
            entry["amount_paid"] += line["amount_paid"]
            entry["amount_pending"] += line["amount_residual"]
        top_salespersons = sorted(
            by_user.values(),
            key=lambda d: d["amount_total"],
            reverse=True,
        )

        # Per-creator aggregation (account.move.create_uid). Same shape
        # as the salesperson breakdown so the dashboard/report can reuse
        # the rendering logic.
        by_creator = {}
        for line in lines:
            key = line["create_user_id"] or 0
            if key not in by_creator:
                by_creator[key] = {
                    "user_id": line["create_user_id"] or False,
                    "user_name": line["create_user_name"] or _("Unassigned"),
                    "count": 0,
                    "amount_total": 0.0,
                    "amount_paid": 0.0,
                    "amount_pending": 0.0,
                }
            entry = by_creator[key]
            entry["count"] += 1
            entry["amount_total"] += line["amount_total"]
            entry["amount_paid"] += line["amount_paid"]
            entry["amount_pending"] += line["amount_residual"]
        top_creators = sorted(
            by_creator.values(),
            key=lambda d: d["amount_total"],
            reverse=True,
        )

        # Trend series — aggregates the dataset on the requested
        # granularity (day / week / month). 'auto' picks based on the
        # span: <=31 days → day, <=120 days → week, else month.
        trend, granularity = self._build_trend(options, lines)
        options["trend_granularity"] = granularity

        # Payment-category aggregates — used by the cash-up workflow
        # ("cuadre diario" / arqueo) and the dashboard charts. We emit
        # both flat totals and a per-bucket trend so the comparative
        # chart can reuse the same series shape as ``trend``.
        payment_totals = self._empty_payment_breakdown()
        payment_trend_buckets = {}
        for line in lines:
            breakdown = line.get("payment_breakdown") or {}
            for category in PAYMENT_CATEGORIES:
                payment_totals[category] += breakdown.get(category, 0.0)
            day = line["invoice_date"]
            if not day:
                continue
            key = self._bucket_key(day, granularity)
            slot = payment_trend_buckets.setdefault(
                key, self._empty_payment_breakdown()
            )
            for category in PAYMENT_CATEGORIES:
                slot[category] += breakdown.get(category, 0.0)
        payment_trend = [
            {"date": k, **v} for k, v in sorted(payment_trend_buckets.items())
        ]
        category_labels = dict(self._get_payment_category_labels())
        payment_categories = [
            {
                "key": cat,
                "label": category_labels[cat],
                "amount": payment_totals[cat],
            }
            for cat in PAYMENT_CATEGORIES
        ]
        payment_total_collected = sum(payment_totals.values())

        # Filter-aware breakdowns shown in the PDF/Excel summary panel.
        # We always emit the same keys so the template can iterate
        # without conditionals.
        by_status = {"paid": 0.0, "pending": 0.0}
        by_status_count = {"paid": 0, "pending": 0}
        for line in lines:
            by_status[line["payment_state"]] = (
                by_status.get(line["payment_state"], 0.0) + line["amount_total"]
            )
            by_status_count[line["payment_state"]] = (
                by_status_count.get(line["payment_state"], 0) + 1
            )

        by_doc_type = {}
        for move in moves:
            doc_type = ""
            if "l10n_latam_document_type_id" in move._fields and move.l10n_latam_document_type_id:
                doc_type = move.l10n_latam_document_type_id.display_name or ""
            if not doc_type:
                doc_type = _("Refund") if move.move_type == "out_refund" else _("Invoice")
            entry = by_doc_type.setdefault(doc_type, {
                "name": doc_type, "count": 0, "amount_total": 0.0,
            })
            entry["count"] += 1
            entry["amount_total"] += move.amount_total
        breakdown_doc_type = sorted(
            by_doc_type.values(), key=lambda d: d["amount_total"], reverse=True,
        )

        # Collection-health snapshot. Compares amount paid vs total
        # invoiced (and count of paid vs total invoices). Status is a
        # traffic-light bucket consumed by the dashboard gauge:
        #   >= 75% paid → good     (green)
        #   >= 50% paid → normal   (yellow)
        #   <  50% paid → bad      (red)
        total_inv = totals["total_invoiced"] or 0.0
        total_paid_amt = totals["total_paid"] or 0.0
        ratio = (total_paid_amt / total_inv) if total_inv else 0.0
        if ratio >= 0.75:
            health_status, health_label = "good", _("Healthy")
        elif ratio >= 0.50:
            health_status, health_label = "normal", _("At Risk")
        else:
            health_status, health_label = "bad", _("Critical")
        paid_count = sum(1 for line in lines if line["payment_state"] == "paid")
        pending_count = sum(1 for line in lines if line["payment_state"] == "pending")
        health = {
            "status": health_status,
            "label": health_label,
            "ratio": ratio,
            "paid_amount": total_paid_amt,
            "pending_amount": totals["total_pending"] or 0.0,
            "paid_count": paid_count,
            "pending_count": pending_count,
            "total_count": totals["count"],
        }

        company = self.env.company
        return {
            "options": options,
            "lines": lines,
            "totals": totals,
            "top_customers": top_customers,
            "top_salespersons": top_salespersons,
            "top_creators": top_creators,
            "trend": trend,
            "health": health,
            "breakdown_status": {
                "paid": {
                    "amount_total": by_status.get("paid", 0.0),
                    "count": by_status_count.get("paid", 0),
                },
                "pending": {
                    "amount_total": by_status.get("pending", 0.0),
                    "count": by_status_count.get("pending", 0),
                },
            },
            "breakdown_doc_type": breakdown_doc_type,
            "payment_categories": payment_categories,
            "payment_totals": payment_totals,
            "payment_total_collected": payment_total_collected,
            "payment_trend": payment_trend,
            "company": {
                "id": company.id,
                "name": company.name,
                "currency_id": company.currency_id.id,
                "currency_symbol": company.currency_id.symbol or company.currency_id.name,
            },
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
        }
