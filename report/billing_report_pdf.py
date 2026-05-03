# -*- coding: utf-8 -*-
"""QWeb data provider for the Billing PDF report.

Odoo's QWeb PDF engine instantiates the abstract model declared in
``ir.actions.report.report_name`` and calls ``_get_report_values`` to
build the rendering context. We delegate the heavy lifting to
:class:`~odoo.addons.custom_billing_report.models.billing_report.BillingReport`
so the PDF, the dashboard and the XLSX export all share the same
dataset.
"""

from odoo import api, models


class ReportBillingPdf(models.AbstractModel):
    _name = "report.custom_billing_report.report_billing_template"
    _description = "Billing Report — PDF data provider"

    @api.model
    def _get_report_values(self, docids, data=None):
        """Return the QWeb rendering context.

        ``data`` is the ``data`` argument passed to ``report_action``.
        It is expected to contain an ``options`` key produced by
        :meth:`billing.report.wizard._get_options`. Falls back to the
        defaults when the report is launched without a wizard (e.g.
        from the developer mode "Print" menu).
        """
        billing_service = self.env["billing.report"]
        options = (data or {}).get("options")
        report_data = billing_service.get_report_data(options)
        return {
            "doc_ids": docids,
            "doc_model": "billing.report.wizard",
            # ``docs`` is included to satisfy QWeb's standard layout
            # but is unused by the template — the report iterates over
            # ``data.lines`` instead.
            "docs": self.env["billing.report.wizard"].browse(docids),
            "data": report_data,
        }
