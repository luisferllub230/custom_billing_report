import io
import json

import xlsxwriter

from odoo import _, http
from odoo.exceptions import AccessError
from odoo.http import content_disposition, request


class BillingReportController(http.Controller):
    @http.route(
        "/custom_billing_report/xlsx",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def download_xlsx(self, options=None, **kwargs):
        """Stream the Excel export.

        :param options: JSON-encoded options dict (same shape as
            :meth:`billing.report.wizard._get_options`). When omitted
            the report uses the service defaults.
        """
        if not request.env.user.has_group("account.group_account_manager"):
            raise AccessError(_("You are not allowed to access the Billing Report."))

        try:
            parsed_options = json.loads(options) if options else {}
        except (TypeError, ValueError):
            parsed_options = {}

        billing_service = request.env["billing.report"].sudo(False)
        data = billing_service.get_report_data(parsed_options)

        xlsx_bytes = self._build_xlsx(data, env=request.env)
        filename = "billing_report_%s_%s.xlsx" % (
            data["options"].get("date_from") or "",
            data["options"].get("date_to") or "",
        )
        return request.make_response(
            xlsx_bytes,
            headers=[
                ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                ("Content-Disposition", content_disposition(filename)),
                ("Content-Length", len(xlsx_bytes)),
            ],
        )

    # ------------------------------------------------------------------
    # Workbook builder
    # ------------------------------------------------------------------

    def _build_xlsx(self, data, env=None):
        """Return the raw bytes of the XLSX file for ``data``.

        We bind a ``context`` local with the user's language so
        :func:`odoo.tools.translate._` can resolve translations by
        inspecting this frame (it looks at ``frame.f_locals['context']``
        before anything else). Without this bridge, ``_()`` would
        fall back to the source string.
        """
        if env is None:
            env = request.env if request else None
        context = {"lang": (env.lang if env else None) or "en_US"}
        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})

        # ---- Formats -------------------------------------------------
        title_fmt = workbook.add_format({
            "bold": True, "font_size": 16, "align": "left",
        })
        meta_fmt = workbook.add_format({"italic": True, "font_color": "#555555"})
        header_fmt = workbook.add_format({
            "bold": True,
            "bg_color": "#1F4E78",
            "font_color": "#FFFFFF",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
        })
        text_fmt = workbook.add_format({"border": 1})
        money_fmt = workbook.add_format({
            "border": 1,
            "num_format": "#,##0.00",
        })
        kpi_label_fmt = workbook.add_format({
            "bold": True, "bg_color": "#E7E6E6", "border": 1,
        })
        kpi_value_fmt = workbook.add_format({
            "bold": True, "border": 1, "num_format": "#,##0.00",
        })
        total_label_fmt = workbook.add_format({
            "bold": True, "bg_color": "#FFF2CC", "border": 1, "align": "right",
        })
        total_value_fmt = workbook.add_format({
            "bold": True, "bg_color": "#FFF2CC", "border": 1,
            "num_format": "#,##0.00",
        })

        # ---- Sheet ---------------------------------------------------
        sheet = workbook.add_worksheet(_("Billing Report"))
        sheet.set_column("A:A", 12)  # Date
        sheet.set_column("B:B", 18)  # Invoice
        sheet.set_column("C:C", 35)  # Customer
        sheet.set_column("D:D", 18)  # NCF
        sheet.set_column("E:H", 14)  # Money
        sheet.set_column("I:I", 22)  # Payment Method
        sheet.set_column("J:J", 16)  # Status

        # Title row
        sheet.merge_range("A1:J1", _("Billing Report"), title_fmt)

        opts = data["options"]
        status_map = {
            "all": _("All"),
            "paid": _("Paid"),
            "unpaid": _("Pending Payment"),
        }
        meta_line = _(
            "Period: %(date_from)s -> %(date_to)s   |   Status: %(status)s",
            date_from=opts.get("date_from") or "",
            date_to=opts.get("date_to") or "",
            status=status_map.get(opts.get("payment_state") or "all", ""),
        )
        sheet.merge_range("A2:J2", meta_line, meta_fmt)

        # KPI block (rows 4-5)
        totals = data["totals"]
        kpi_pairs = [
            (_("Invoices"), totals["count"]),
            (_("Total Invoiced"), totals["total_invoiced"]),
            (_("Total Paid"), totals["total_paid"]),
            (_("Total Pending"), totals["total_pending"]),
            (_("Total Tax"), totals["total_tax"]),
            (_("Total Discount"), totals["total_discount"]),
        ]
        for idx, (label, value) in enumerate(kpi_pairs):
            sheet.write(3, idx, label, kpi_label_fmt)
            sheet.write(4, idx, value, kpi_value_fmt)

        # Detail header (row 7 — index 6)
        headers = [
            _("Date"), _("Invoice"), _("Customer"), _("NCF"),
            _("Subtotal"), _("Discount"), _("Tax (ITBIS)"), _("Total"),
            _("Payment Method"), _("Status"),
        ]
        header_row = 6
        for col, label in enumerate(headers):
            sheet.write(header_row, col, label, header_fmt)
        sheet.set_row(header_row, 22)
        sheet.freeze_panes(header_row + 1, 0)
        sheet.autofilter(header_row, 0, header_row, len(headers) - 1)

        # Detail rows
        row = header_row + 1
        for line in data["lines"]:
            sheet.write(row, 0, line["invoice_date"], text_fmt)
            sheet.write(row, 1, line["name"], text_fmt)
            sheet.write(row, 2, line["partner_name"], text_fmt)
            sheet.write(row, 3, line["ncf"], text_fmt)
            sheet.write_number(row, 4, line["amount_untaxed"], money_fmt)
            sheet.write_number(row, 5, line["discount"], money_fmt)
            sheet.write_number(row, 6, line["amount_tax"], money_fmt)
            sheet.write_number(row, 7, line["amount_total"], money_fmt)
            sheet.write(row, 8, line["payment_method"], text_fmt)
            sheet.write(row, 9, line["status_label"], text_fmt)
            row += 1

        # Totals row
        sheet.merge_range(row, 0, row, 3, _("Totals"), total_label_fmt)
        sheet.write_number(row, 4, totals["total_untaxed"], total_value_fmt)
        sheet.write_number(row, 5, totals["total_discount"], total_value_fmt)
        sheet.write_number(row, 6, totals["total_tax"], total_value_fmt)
        sheet.write_number(row, 7, totals["total_invoiced"], total_value_fmt)
        sheet.write(row, 8, "", total_value_fmt)
        sheet.write(row, 9, "", total_value_fmt)

        workbook.close()
        return buffer.getvalue()
