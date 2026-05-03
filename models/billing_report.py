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

from datetime import date, timedelta

from odoo import _, api, fields, models


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Move types considered as customer billing for this report.
CUSTOMER_MOVE_TYPES = ("out_invoice", "out_refund")

#: ``payment_state`` values that we treat as "fully paid" for KPI purposes.
PAID_STATES = ("paid", "in_payment", "reversed")

#: ``payment_state`` values that we treat as "still owed".
UNPAID_STATES = ("not_paid", "partial")


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

    @api.model
    def _get_discount_amount(self, move):
        """Sum of line discounts (in company currency) for ``move``.

        Computed from ``invoice_line_ids`` so it ignores tax / payment
        terms / section lines.
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
    def _get_itbis_amount(self, move):
        """Return the ITBIS portion of ``amount_tax``.

        We consider a tax to be ITBIS when its name contains the
        substring "ITBIS" (case-insensitive). When no such tax is
        found we fall back to the full ``amount_tax`` so the column is
        never empty on non-RD installs.
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
            return move.amount_tax
        # Match the sign convention of refunds.
        return -itbis if move.move_type == "out_refund" else itbis

    @api.model
    def _get_payment_method_label(self, move):
        """Comma-separated list of payment-method names on ``move``."""
        if "matched_payment_ids" not in move._fields:
            return ""
        payments = move.matched_payment_ids
        names = payments.mapped("payment_method_line_id.name")
        return ", ".join(n for n in names if n)

    @api.model
    def _get_status(self, move):
        """Return ``(code, label)`` for the human-readable payment state."""
        if move.payment_state in PAID_STATES:
            return "paid", _("Paid")
        return "pending", _("Pending Payment")

    @api.model
    def _build_line(self, move):
        """Render a single move into the report row dict."""
        status_code, status_label = self._get_status(move)
        return {
            "id": move.id,
            "invoice_date": fields.Date.to_string(move.invoice_date) if move.invoice_date else "",
            "name": move.name or "",
            "partner_id": move.partner_id.id,
            "partner_name": move.partner_id.display_name or "",
            "ncf": self._get_ncf(move),
            "amount_untaxed": move.amount_untaxed,
            "discount": self._get_discount_amount(move),
            "amount_tax": self._get_itbis_amount(move),
            "amount_total": move.amount_total,
            "amount_residual": move.amount_residual,
            "amount_paid": move.amount_total - move.amount_residual,
            "payment_method": self._get_payment_method_label(move),
            "payment_state": status_code,
            "status_label": status_label,
            "currency_id": move.currency_id.id,
            "currency_symbol": move.currency_id.symbol or move.currency_id.name,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @api.model
    def get_report_data(self, options=None):
        """Return ``{options, lines, totals, ...}`` for the given filters.

        The returned dict is JSON-serialisable so it can be sent
        verbatim to the OWL dashboard via RPC.
        """
        options = self._normalize_options(options)
        domain = self._get_invoice_domain(options)
        moves = self.env["account.move"].search(
            domain, order="invoice_date asc, name asc"
        )

        lines = [self._build_line(m) for m in moves]

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
                    "amount_pending": 0.0,
                }
            by_partner[key]["amount_total"] += line["amount_total"]
            by_partner[key]["amount_pending"] += line["amount_residual"]

        top_customers = sorted(
            by_partner.values(),
            key=lambda d: d["amount_total"],
            reverse=True,
        )[:10]

        # Daily trend series — feeds the Chart.js visualization on the
        # dashboard. Aggregated server-side to keep the OWL renderer
        # cheap and to keep the same numbers across PDF/XLSX/dashboard.
        by_day = {}
        for line in lines:
            day = line["invoice_date"]
            if not day:
                continue
            bucket = by_day.setdefault(day, {
                "invoiced": 0.0,
                "paid": 0.0,
                "pending": 0.0,
            })
            bucket["invoiced"] += line["amount_total"]
            bucket["paid"] += line["amount_paid"]
            bucket["pending"] += line["amount_residual"]
        trend = [
            {"date": day, **values}
            for day, values in sorted(by_day.items())
        ]

        company = self.env.company
        return {
            "options": options,
            "lines": lines,
            "totals": totals,
            "top_customers": top_customers,
            "trend": trend,
            "company": {
                "id": company.id,
                "name": company.name,
                "currency_id": company.currency_id.id,
                "currency_symbol": company.currency_id.symbol or company.currency_id.name,
            },
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
        }
