{
    "name": "Custom Billing Report",
    "version": "19.0.1.0.0",
    "category": "Accounting/Reporting",
    "summary": (
        "Detailed billing reports with interactive dashboard, "
        "PDF and Excel export."
    ),
    "description": """
Custom Billing Report
=====================
Generic, reusable billing report module that produces:

* An interactive dashboard (KPI cards, filters, table).
* Per-invoice detail rows: invoice date, sequence, customer, NCF,
  subtotal, discount, tax (ITBIS), total, payment method, status.
* Totalizers: total invoiced, pending, paid, total tax, total
  discount.
* Date-range presets (today, yesterday, this week, this/last month,
  custom).
* Filters: customer, status, payment method, company, NCF type.
* PDF export (QWeb) and Excel export (xlsxwriter) launched directly
  from the dashboard.

Designed to be installed on top of any Odoo 19 instance with
``account``. The Dominican Republic NCF columns are populated when
``l10n_latam_invoice_document`` / ``l10n_do_accounting`` are present;
the module degrades gracefully on instances without them.
""",
    "author": "Odoo Localización RD",
    "website": "https://github.com/luisferllub230/odoo_localizacion",
    "license": "LGPL-3",
    "depends": [
        "account",
        "web",
        # Optional but assumed in the target environment — provides
        # ``l10n_latam.document.type`` (the NCF type catalogue).
        "l10n_latam_invoice_document",
    ],
    "data": [
        "security/ir.model.access.csv",
        "wizard/billing_report_wizard_views.xml",
        "views/billing_dashboard_views.xml",
        "views/billing_report_menus.xml",
        "report/billing_report_actions.xml",
        "report/billing_report_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "custom_billing_report/static/src/scss/billing_dashboard.scss",
            "custom_billing_report/static/src/js/billing_trend_chart.js",
            "custom_billing_report/static/src/js/billing_dashboard.js",
            "custom_billing_report/static/src/xml/billing_trend_chart.xml",
            "custom_billing_report/static/src/xml/billing_dashboard.xml",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
