=====================
Custom Billing Report
=====================

.. |badge_license| image:: https://img.shields.io/badge/licence-LGPL--3-blue.png
   :target: https://www.gnu.org/licenses/lgpl-3.0-standalone.html
   :alt: License: LGPL-3
.. |badge_odoo| image:: https://img.shields.io/badge/odoo-18.0-875A7B.png
   :alt: Odoo 18.0

|badge_license| |badge_odoo|

Custom Billing Report is a generic, reusable billing-report module for
Odoo 18. It produces a single, consistent dataset that is rendered
through three output channels:

* an interactive OWL dashboard (KPI cards, daily-trend chart, sortable
  detail table, top-customers panel),
* a QWeb PDF report,
* an Excel (XLSX) export, served from a dedicated HTTP controller.

The module ships with first-class support for the Dominican Republic
NCF (``Numero de Comprobante Fiscal``) and the local ITBIS tax, but
**degrades gracefully** on plain Odoo installs: when the Dominican
localization is not available the relevant columns are simply blank
and the rest of the report works untouched.

.. contents::
   :local:

Features
========

* Date-range presets: today, yesterday, this week, this month, last
  month, plus a free custom range.
* Filters: customer, payment status, payment method, company, NCF
  document type.
* Per-invoice detail rows: invoice date, sequence, customer, NCF,
  subtotal, discount, tax (ITBIS), total, payment method, status.
* Aggregated totals: total invoiced, total paid, total pending, total
  tax, total discount.
* Daily-trend chart (Chart.js) with stacked bars (paid / pending) and a
  line for the total invoiced amount.
* Top-10 customers panel (by total invoiced).
* Payment-type cash-up panel ("cuadre diario" / arqueo) — splits
  collected amounts into four buckets (Cash, Card, Transfer, Bank) and
  renders both a per-bucket trend chart and a comparative chart
  (donut + stacked bar).
* PDF and Excel exports launched directly from the dashboard.
* Single source of truth — the dashboard, the PDF and the XLSX share
  the **same** dataset, so totals and per-line values stay in sync
  across every output channel.

Requirements
============

* Odoo 18.0
* Python ``xlsxwriter`` (already part of Odoo's Python requirements)

Dependencies
============

* ``account``
* ``web``
* ``l10n_latam_invoice_document`` — required to expose the NCF type
  filter; the module loads on instances without it but the NCF column
  will be empty and the document-type filter is hidden.

Installation
============

#. Drop the ``custom_billing_report`` folder into your Odoo addons
   path.
#. Update the apps list (Apps -> Update Apps List).
#. Search for "Custom Billing Report" and install it.

Usage
=====

User Guide
----------

Generate a report
~~~~~~~~~~~~~~~~~

#. Open *Billing Report -> Generate Report* (or *Accounting ->
   Reporting -> Billing Dashboard*).
#. Pick a date-range preset, or switch to *Custom* and choose your own
   ``From`` / ``To`` dates.
#. Optionally narrow the result with the side filters (customer,
   status, payment method, company, NCF type).
#. Click one of the three actions:

   * **Open Dashboard** — opens the interactive OWL dashboard.
   * **Print PDF** — renders the QWeb PDF report.
   * **Download Excel** — streams the XLSX export.

Use the dashboard
~~~~~~~~~~~~~~~~~

The dashboard exposes:

* a filter bar (date pickers, status selector, quick-range buttons,
  *Apply* button),
* six KPI cards (Invoices count, Total Invoiced, Total Paid, Total
  Pending, Total Tax, Total Discount),
* a daily-trend chart,
* a sortable, paginated detail table — clicking a row opens the
  underlying invoice form,
* a *Top Customers* panel.

Use the *Refresh* button (control-panel) to re-run the query without
changing filters; the *Export* dropdown launches the same PDF and XLSX
exports as the wizard.

User access
~~~~~~~~~~~

The wizard is reachable by every user belonging to:

* ``account.group_account_invoice`` (Billing) — read/write/create access,
* ``account.group_account_manager`` (Accounting Manager).

Localization (Spanish - Dominican Republic)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A pre-translated ``i18n/es_DO.po`` is shipped with the module. To load
it:

#. Activate developer mode.
#. Settings -> Translations -> Languages -> ensure *Spanish (DO) /
   Espanol (DR)* is active.
#. Settings -> Translations -> Import / Export -> Import Translation
   and select ``custom_billing_report/i18n/es_DO.po``.

Technical Guide
---------------

Architecture
~~~~~~~~~~~~

The module is built around a single abstract service model
(``billing.report``) that owns every piece of business logic. The
three output channels (dashboard / PDF / XLSX) are thin renderers on
top of that service, so the data is computed only once and the three
outputs cannot drift apart. ::

    +----------------------+        +----------------------------+
    | billing.report.wizard|------->| billing.report (service)   |
    +----------------------+        +-------------+--------------+
              |                                   |
              | options dict                      | get_report_data()
              v                                   v
       +------+------+   +-----------+   +------------------+
       |   Dashboard |   |  QWeb PDF |   | XLSX controller  |
       |  (OWL/RPC)  |   |  template |   | (xlsxwriter)     |
       +-------------+   +-----------+   +------------------+

Models
~~~~~~

``billing.report`` *(AbstractModel)*
   Public entry point: ``get_report_data(options)``.

   Returns a JSON-serialisable payload::

       {
           "options":          <normalised filter dict>,
           "lines":            [<per-invoice row>, ...],
           "totals":           {<aggregated KPIs>},
           "top_customers":    [<top-10 by total>, ...],
           "trend":            [<{date, invoiced, paid, pending}>, ...],
           "payment_categories":   [<{key, label, amount}>, ...],
           "payment_totals":       {<{cash, card, transfer, bank, other}>},
           "payment_total_collected": <float>,
           "payment_trend":    [<{date, cash, card, transfer, bank, other}>, ...],
           "company":          {<id, name, currency_id, currency_symbol>},
           "generated_at":     <ISO timestamp>,
       }

   Supporting helpers:

   * ``_default_options()`` — canonical filter dict, used as fallback
     when callers omit keys.
   * ``_normalize_options(options)`` — merges incoming filters over
     the defaults.
   * ``_get_invoice_domain(options)`` — composes the
     ``account.move`` search domain.
   * ``_build_line(move)`` — renders a single move into the row dict.
   * ``_get_ncf(move)``, ``_get_discount_amount(move)``,
     ``_get_itbis_amount(move)``, ``_get_payment_method_label(move)``,
     ``_get_status(move)`` — column-level extractors.
   * ``_classify_payment(payment)`` — bucket an
     ``account.payment`` into one of *cash / card / transfer / bank /
     other*. Resolution order:

     #. ``journal.type == 'cash'`` always maps to *cash*.
     #. The payment-method-line name and the underlying payment
        method ``code`` / ``name`` are scanned against the keyword
        buckets in ``PAYMENT_CATEGORY_KEYWORDS`` (card / transfer).
     #. ``journal.type == 'bank'`` defaults to *bank*.
     #. Anything else falls back to *other*.

   * ``_get_payment_breakdown(move)`` — sum each reconciled payment
     into the corresponding bucket; refunds are signed negative.

``billing.report.wizard`` *(TransientModel)*
   UI entry point. Converts wizard fields into the canonical
   ``options`` dict and exposes three actions:

   * ``action_view_dashboard`` -> ``ir.actions.client``
   * ``action_print_pdf`` -> ``ir.actions.report``
   * ``action_print_xlsx`` -> ``ir.actions.act_url``

``report.custom_billing_report.report_billing_template``
   QWeb data provider for the PDF report. Delegates to
   ``billing.report.get_report_data`` and forwards the result as
   ``data``.

Controller
~~~~~~~~~~

``GET /custom_billing_report/xlsx``
   Auth: ``user``. Query string:

   * ``options`` *(optional)* — JSON-encoded options dict, same shape
     as ``billing.report.wizard._get_options``.

   Streams an XLSX file with three regions:

   * a header (title + period meta),
   * a 6-column KPI block,
   * a detail table with autofilter, frozen header, and a totals row.

Options dict
~~~~~~~~~~~~

The single payload accepted by every layer of the module::

    {
        "date_from":                    "YYYY-MM-DD",
        "date_to":                      "YYYY-MM-DD",
        "partner_ids":                  [int, ...],
        "payment_state":                "all" | "paid" | "unpaid",
        "payment_method_ids":           [int, ...],
        "company_ids":                  [int, ...],
        "l10n_latam_document_type_ids": [int, ...],
    }

Frontend (OWL)
~~~~~~~~~~~~~~

* ``custom_billing_report.dashboard`` — client action tag
  (``BillingDashboard`` component).
* ``BillingTrendChart`` — Chart.js wrapper. Chart.js is loaded on
  demand through ``loadBundle("web.chartjs_lib")``, the same pattern
  used by Odoo's graph view.
* Styles are theme-aware: they lean on Bootstrap 5.3 / Odoo CSS
  variables and ship a small ``[data-bs-theme="dark"]`` block.

Extending
~~~~~~~~~

Add a custom column
^^^^^^^^^^^^^^^^^^^

#. Override ``billing.report._build_line`` to add the new key::

       def _build_line(self, move):
           line = super()._build_line(move)
           line["my_custom_field"] = move.x_my_field
           return line

#. Add a ``<th>`` and ``<td>`` to ``billing_report_templates.xml`` for
   the PDF.
#. Add an entry to ``columns`` in ``billing_dashboard.js`` and a
   matching cell in the OWL template.
#. Add a ``sheet.write(...)`` line in
   ``BillingReportController._build_xlsx``.

Add a custom filter
^^^^^^^^^^^^^^^^^^^

#. Add a key to ``billing.report._default_options``.
#. Branch on it inside ``billing.report._get_invoice_domain``.
#. Add a corresponding field to ``billing.report.wizard`` and surface
   it in the dashboard filter bar.

Testing
=======

The ``tests/`` package covers every layer of the module. Run them with::

    odoo-bin -c <config> -d <db> -i custom_billing_report \
             --test-enable --stop-after-init \
             --log-level=test

Test files:

* ``test_billing_report.py`` — service-level tests (options, domain,
  per-row builders, aggregate totals, trend, JSON-serialisability).
* ``test_billing_report_wizard.py`` — wizard tests (defaults,
  ``_onchange_date_range`` presets, date constraint, ``_get_options``,
  ``action_view_dashboard``, ``action_print_pdf``,
  ``action_print_xlsx``).
* ``test_billing_report_controller.py`` — workbook-builder unit tests
  plus an ``HttpCase`` that hits ``/custom_billing_report/xlsx`` end
  to end.

Internationalisation
====================

* All user-facing strings are written in English in source. Python
  strings use ``_()``, JavaScript strings use ``_t()``, and QWeb /
  XML templates expose plain text that Odoo's translation extractor
  picks up automatically.
* A pre-translated catalogue for Spanish (Dominican Republic) is
  shipped in ``i18n/es_DO.po`` and can be loaded as described in the
  *Localization* section above.
* To regenerate ``i18n/custom_billing_report.pot`` after adding new
  strings::

      odoo-bin -c <config> -d <db> --modules=custom_billing_report \
               --i18n-export=custom_billing_report.pot \
               --stop-after-init

Bug Tracker
===========

Bugs and feature requests are tracked on GitHub:
https://github.com/luisferllub230/odoo_localizacion/issues

Credits
=======

Authors
-------

* Luis Fernandez (Odoo Localizacion RD)

Maintainers
-----------

* `@luisferllub230 <https://github.com/luisferllub230>`_

License
=======

This module is licensed under the LGPL-3 license. See the
`LICENSE <https://www.gnu.org/licenses/lgpl-3.0-standalone.html>`_
file for details.
