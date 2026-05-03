# -*- coding: utf-8 -*-
"""Billing Report Wizard.

Lets the user pick the filters (date range, customers, status,
payment method, companies, NCF type) and choose how to consume the
result:

* :meth:`action_view_dashboard` — open the OWL dashboard.
* :meth:`action_print_pdf`     — render the QWeb PDF.
* :meth:`action_print_xlsx`    — download the Excel export.

The wizard converts its UI fields into the canonical ``options`` dict
expected by :class:`~odoo.addons.custom_billing_report.models.billing_report.BillingReport`.
"""

import json
from datetime import timedelta
from urllib.parse import urlencode

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BillingReportWizard(models.TransientModel):
    _name = "billing.report.wizard"
    _description = "Billing Report Wizard"

    # ------------------------------------------------------------------
    # Date range
    # ------------------------------------------------------------------

    date_range = fields.Selection(
        selection=[
            ("today", "Today"),
            ("yesterday", "Yesterday"),
            ("this_week", "This Week"),
            ("this_month", "This Month"),
            ("last_month", "Last Month"),
            ("custom", "Custom"),
        ],
        string="Date Range",
        default="this_month",
        required=True,
    )
    date_from = fields.Date(string="From", required=True)
    date_to = fields.Date(string="To", required=True)

    # ------------------------------------------------------------------
    # Optional filters
    # ------------------------------------------------------------------

    partner_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="billing_report_wizard_partner_rel",
        column1="wizard_id",
        column2="partner_id",
        string="Customers",
        help="Leave empty to include every customer.",
    )
    payment_state = fields.Selection(
        selection=[
            ("all", "All"),
            ("paid", "Paid"),
            ("unpaid", "Pending Payment"),
        ],
        string="Status",
        default="all",
        required=True,
    )
    payment_method_ids = fields.Many2many(
        comodel_name="account.payment.method.line",
        relation="billing_report_wizard_pmethod_rel",
        column1="wizard_id",
        column2="method_id",
        string="Payment Methods",
        help="Leave empty to include every payment method.",
    )
    company_ids = fields.Many2many(
        comodel_name="res.company",
        relation="billing_report_wizard_company_rel",
        column1="wizard_id",
        column2="company_id",
        string="Companies",
        default=lambda self: self.env.companies.ids,
        required=True,
    )
    l10n_latam_document_type_ids = fields.Many2many(
        comodel_name="l10n_latam.document.type",
        relation="billing_report_wizard_doctype_rel",
        column1="wizard_id",
        column2="doc_type_id",
        string="NCF Types",
        help="Filter by Dominican Republic NCF type. Only available "
             "when l10n_latam_invoice_document is installed.",
    )

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        """Pre-fill the date pickers with the current month."""
        res = super().default_get(fields_list)
        today = fields.Date.context_today(self)
        first = today.replace(day=1)
        next_month = (first + timedelta(days=32)).replace(day=1)
        res.setdefault("date_from", first)
        res.setdefault("date_to", next_month - timedelta(days=1))
        return res

    # ------------------------------------------------------------------
    # Onchange — keep dates consistent with the preset selector
    # ------------------------------------------------------------------

    @api.onchange("date_range")
    def _onchange_date_range(self):
        """Recompute ``date_from`` / ``date_to`` from the preset."""
        if self.date_range == "custom":
            return
        today = fields.Date.context_today(self)
        if self.date_range == "today":
            self.date_from = self.date_to = today
        elif self.date_range == "yesterday":
            yesterday = today - timedelta(days=1)
            self.date_from = self.date_to = yesterday
        elif self.date_range == "this_week":
            week_start = today - timedelta(days=today.weekday())
            self.date_from = week_start
            self.date_to = week_start + timedelta(days=6)
        elif self.date_range == "this_month":
            first = today.replace(day=1)
            next_month = (first + timedelta(days=32)).replace(day=1)
            self.date_from = first
            self.date_to = next_month - timedelta(days=1)
        elif self.date_range == "last_month":
            first_this = today.replace(day=1)
            last_prev = first_this - timedelta(days=1)
            self.date_from = last_prev.replace(day=1)
            self.date_to = last_prev

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for wiz in self:
            if wiz.date_from and wiz.date_to and wiz.date_from > wiz.date_to:
                raise UserError(_("'From' date cannot be later than 'To' date."))

    # ------------------------------------------------------------------
    # Options serialisation
    # ------------------------------------------------------------------

    def _get_options(self):
        """Return the canonical ``options`` payload for this wizard."""
        self.ensure_one()
        return {
            "date_from": fields.Date.to_string(self.date_from),
            "date_to": fields.Date.to_string(self.date_to),
            "partner_ids": self.partner_ids.ids,
            "payment_state": self.payment_state,
            "payment_method_ids": self.payment_method_ids.ids,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "l10n_latam_document_type_ids": self.l10n_latam_document_type_ids.ids,
        }

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_view_dashboard(self):
        """Open the OWL dashboard pre-loaded with the wizard filters."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "custom_billing_report.dashboard",
            "name": _("Billing Dashboard"),
            "params": {"options": self._get_options()},
            "target": "current",
        }

    def action_print_pdf(self):
        """Render the QWeb PDF report with the wizard filters."""
        self.ensure_one()
        data = {"options": self._get_options()}
        return self.env.ref(
            "custom_billing_report.action_report_billing"
        ).report_action(self, data=data)

    def action_print_xlsx(self):
        """Download the Excel export with the wizard filters."""
        self.ensure_one()
        params = urlencode({"options": json.dumps(self._get_options())})
        return {
            "type": "ir.actions.act_url",
            "url": "/custom_billing_report/xlsx?%s" % params,
            "target": "new",
        }
