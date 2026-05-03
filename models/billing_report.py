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
            "invoice_user_id": move.invoice_user_id.id if move.invoice_user_id else False,
            "invoice_user_name": move.invoice_user_id.display_name or "",
            "create_user_id": move.create_uid.id if move.create_uid else False,
            "create_user_name": move.create_uid.display_name or "",
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
            "company": {
                "id": company.id,
                "name": company.name,
                "currency_id": company.currency_id.id,
                "currency_symbol": company.currency_id.symbol or company.currency_id.name,
            },
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
        }
