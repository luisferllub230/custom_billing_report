/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { formatMonetary } from "@web/views/fields/formatters";
import { Layout } from "@web/search/layout";
import { Pager } from "@web/core/pager/pager";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { BillingTrendChart } from "./billing_trend_chart";
import { BillingHealthGauge } from "./billing_health_gauge";

const PAGE_SIZE = 50;

export class BillingDashboard extends Component {
    static template = "custom_billing_report.BillingDashboard";
    static components = {
        Layout, Pager, Dropdown, DropdownItem,
        BillingTrendChart, BillingHealthGauge,
    };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        const initialOptions =
            (this.props.action && this.props.action.params && this.props.action.params.options) ||
            {};

        this.state = useState({
            loading: true,
            options: {
                date_from: initialOptions.date_from || "",
                date_to: initialOptions.date_to || "",
                payment_state: initialOptions.payment_state || "all",
                partner_ids: initialOptions.partner_ids || [],
                payment_method_ids: initialOptions.payment_method_ids || [],
                company_ids: initialOptions.company_ids || [],
                l10n_latam_document_type_ids: initialOptions.l10n_latam_document_type_ids || [],
                active_preset: initialOptions.active_preset || "",
                trend_granularity: initialOptions.trend_granularity || "auto",
            },
            sort: { field: "invoice_date", dir: "asc" },
            pager: { offset: 0, limit: PAGE_SIZE },
            data: {
                lines: [],
                trend: [],
                totals: {
                    count: 0,
                    total_invoiced: 0,
                    total_paid: 0,
                    total_pending: 0,
                    total_tax: 0,
                    total_discount: 0,
                    total_untaxed: 0,
                },
                top_customers: [],
                health: {
                    status: "neutral", label: "",
                    ratio: 0,
                    paid_amount: 0, pending_amount: 0,
                    paid_count: 0, pending_count: 0, total_count: 0,
                },
                company: { currency_id: false, currency_symbol: "" },
            },
        });

        this.presets = [
            { key: "today", label: _t("Today") },
            { key: "yesterday", label: _t("Yesterday") },
            { key: "this_week", label: _t("This Week") },
            { key: "this_month", label: _t("This Month") },
            { key: "last_month", label: _t("Last Month") },
        ];

        this.granularities = [
            { key: "auto", label: _t("Auto") },
            { key: "day", label: _t("Day") },
            { key: "week", label: _t("Week") },
            { key: "month", label: _t("Month") },
        ];

        this.columns = [
            { key: "invoice_date", label: _t("Date") },
            { key: "name", label: _t("Invoice") },
            { key: "partner_name", label: _t("Customer") },
            { key: "ncf", label: _t("NCF") },
            { key: "amount_untaxed", label: _t("Subtotal"), numeric: true },
            { key: "discount", label: _t("Discount"), numeric: true },
            { key: "amount_tax", label: _t("Tax (ITBIS)"), numeric: true },
            { key: "amount_total", label: _t("Total"), numeric: true },
            { key: "payment_method", label: _t("Payment Method") },
            { key: "payment_state", label: _t("Status") },
        ];

        onWillStart(async () => {
            await this.loadData();
        });
    }

    get display() {
        return { controlPanel: {} };
    }

    async loadData() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(
                "billing.report",
                "get_report_data",
                [this.state.options]
            );
            this.state.data = data;
            this.state.pager.offset = 0;
            Object.assign(this.state.options, data.options);
        } catch (error) {
            this.notification.add(
                _t("Could not load billing data: %s", error.message || error),
                { type: "danger" }
            );
        } finally {
            this.state.loading = false;
        }
    }

    formatMoney(value) {
        const currencyId = this.state.data.company && this.state.data.company.currency_id;
        if (!currencyId) {
            return Number(value || 0).toFixed(2);
        }
        return formatMonetary(value || 0, { currencyId });
    }

    get currencyId() {
        return this.state.data.company && this.state.data.company.currency_id;
    }

    // ----------------------------------------------------------------
    // Sorting + paging
    // ----------------------------------------------------------------

    get sortedLines() {
        const { field, dir } = this.state.sort;
        const lines = [...(this.state.data.lines || [])];
        const mult = dir === "asc" ? 1 : -1;
        lines.sort((a, b) => {
            const av = a[field];
            const bv = b[field];
            if (typeof av === "number" && typeof bv === "number") {
                return (av - bv) * mult;
            }
            return String(av || "").localeCompare(String(bv || "")) * mult;
        });
        return lines;
    }

    get pagedLines() {
        const { offset, limit } = this.state.pager;
        return this.sortedLines.slice(offset, offset + limit);
    }

    onPagerUpdate({ offset, limit }) {
        this.state.pager.offset = offset;
        this.state.pager.limit = limit;
    }

    onSortBy(field) {
        if (this.state.sort.field === field) {
            this.state.sort.dir = this.state.sort.dir === "asc" ? "desc" : "asc";
        } else {
            this.state.sort.field = field;
            this.state.sort.dir = "asc";
        }
        this.state.pager.offset = 0;
    }

    // ----------------------------------------------------------------
    // Active filter chips
    // ----------------------------------------------------------------

    get activeChips() {
        const chips = [];
        const o = this.state.options;
        if (o.date_from || o.date_to) {
            const from = o.date_from || "…";
            const to = o.date_to || "…";
            chips.push({ key: "dates", label: _t("Date: %s → %s", from, to) });
        }
        if (o.payment_state && o.payment_state !== "all") {
            const map = { paid: _t("Paid"), unpaid: _t("Pending") };
            chips.push({ key: "payment_state", label: _t("Status: %s", map[o.payment_state]) });
        }
        return chips;
    }

    removeFilter(key) {
        if (key === "dates") {
            this.state.options.date_from = "";
            this.state.options.date_to = "";
            this.state.options.active_preset = "";
        } else if (key === "payment_state") {
            this.state.options.payment_state = "all";
        }
        this.loadData();
    }

    // ----------------------------------------------------------------
    // Filter handlers
    // ----------------------------------------------------------------

    onDateFromChange(ev) {
        this.state.options.date_from = ev.target.value;
        this.state.options.active_preset = "";
    }

    onDateToChange(ev) {
        this.state.options.date_to = ev.target.value;
        this.state.options.active_preset = "";
    }

    onPaymentStateChange(ev) {
        this.state.options.payment_state = ev.target.value;
    }

    applyPreset(preset) {
        const today = new Date();
        const fmt = (d) => d.toISOString().slice(0, 10);
        let from, to;
        if (preset === "today") {
            from = to = today;
        } else if (preset === "yesterday") {
            from = to = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
        } else if (preset === "this_week") {
            const day = today.getDay() || 7;
            from = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (day - 1));
            to = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 6);
        } else if (preset === "this_month") {
            from = new Date(today.getFullYear(), today.getMonth(), 1);
            to = new Date(today.getFullYear(), today.getMonth() + 1, 0);
        } else if (preset === "last_month") {
            from = new Date(today.getFullYear(), today.getMonth() - 1, 1);
            to = new Date(today.getFullYear(), today.getMonth(), 0);
        } else {
            return;
        }
        this.state.options.date_from = fmt(from);
        this.state.options.date_to = fmt(to);
        this.state.options.active_preset = preset;
        this.loadData();
    }

    onApplyFilters() {
        this.loadData();
    }

    setGranularity(granularity) {
        this.state.options.trend_granularity = granularity;
        this.loadData();
    }

    // ----------------------------------------------------------------
    // Row navigation — open the underlying invoice form.
    // ----------------------------------------------------------------

    onOpenInvoice(moveId) {
        if (!moveId) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "account.move",
            res_id: moveId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ----------------------------------------------------------------
    // Export actions
    // ----------------------------------------------------------------

    async onPrintPdf() {
        await this.action.doAction({
            type: "ir.actions.report",
            report_type: "qweb-pdf",
            report_name: "custom_billing_report.report_billing_template",
            data: { options: this.state.options },
        });
    }

    onPrintXlsx() {
        const params = new URLSearchParams({
            options: JSON.stringify(this.state.options),
        });
        const url = `/custom_billing_report/xlsx?${params.toString()}`;
        window.open(url, "_blank");
    }
}

registry
    .category("actions")
    .add("custom_billing_report.dashboard", BillingDashboard);
