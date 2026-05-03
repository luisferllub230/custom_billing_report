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
            "docs": self.env["billing.report.wizard"].browse(docids),
            "data": report_data,
        }
