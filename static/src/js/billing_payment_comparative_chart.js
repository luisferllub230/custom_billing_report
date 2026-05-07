/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { formatMonetary } from "@web/views/fields/formatters";

/**
 * Comparative chart for the four payment buckets. Renders two
 * sub-charts side-by-side:
 *
 *   * doughnut — share of total collected per category,
 *   * stacked-bar — same buckets across time so the cashier can
 *     spot a sudden shift (e.g. card up, cash down).
 */
export class BillingPaymentComparativeChart extends Component {
    static template = "custom_billing_report.BillingPaymentComparativeChart";
    static props = {
        categories: { type: Array },
        trend: { type: Array },
        currencyId: { type: [Number, Boolean], optional: true },
    };

    setup() {
        this.donutRef = useRef("donut");
        this.barRef = useRef("bar");
        onWillStart(() => loadBundle("web.chartjs_lib"));
        useEffect(
            () => {
                this._renderCharts();
                return () => this._destroyCharts();
            },
            () => [this.props.categories, this.props.trend, this.props.currencyId]
        );
        onWillUnmount(() => this._destroyCharts());
    }

    _destroyCharts() {
        for (const key of ["donut", "bar"]) {
            if (this[`_chart_${key}`]) {
                this[`_chart_${key}`].destroy();
                this[`_chart_${key}`] = null;
            }
        }
    }

    formatMoney(value) {
        if (this.props.currencyId) {
            return formatMonetary(value || 0, { currencyId: this.props.currencyId });
        }
        return Number(value || 0).toFixed(2);
    }

    get colors() {
        // Stable category->colour mapping. Kept aligned with
        // BillingDashboard.paymentCategoryConfig so per-category and
        // comparative charts share the same palette.
        return {
            cash: "#198754",
            card: "#0d6efd",
            transfer: "#6f42c1",
            bank: "#fd7e14",
            other: "#6c757d",
        };
    }

    _renderCharts() {
        if (typeof Chart === "undefined") {
            return;
        }
        this._destroyCharts();
        this._renderDonut();
        this._renderBar();
    }

    _renderDonut() {
        if (!this.donutRef.el) {
            return;
        }
        const cats = (this.props.categories || []).filter((c) => (c.amount || 0) !== 0);
        const labels = cats.map((c) => c.label);
        const data = cats.map((c) => c.amount || 0);
        const colors = cats.map((c) => this.colors[c.key] || "#6c757d");
        const ctx = this.donutRef.el.getContext("2d");
        this._chart_donut = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels,
                datasets: [{
                    data,
                    backgroundColor: colors,
                    borderColor: "#ffffff",
                    borderWidth: 2,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: "bottom" },
                    tooltip: {
                        callbacks: {
                            label: (ctx) =>
                                `${ctx.label}: ${this.formatMoney(ctx.parsed)}`,
                        },
                    },
                },
            },
        });
    }

    _renderBar() {
        if (!this.barRef.el) {
            return;
        }
        const trend = this.props.trend || [];
        const labels = trend.map((p) => p.date);
        const cats = (this.props.categories || []);
        const datasets = cats.map((c) => ({
            label: c.label,
            data: trend.map((p) => p[c.key] || 0),
            backgroundColor: this.colors[c.key] || "#6c757d",
            stack: "categories",
        }));
        const ctx = this.barRef.el.getContext("2d");
        this._chart_bar = new Chart(ctx, {
            type: "bar",
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: "bottom" },
                    tooltip: {
                        callbacks: {
                            label: (ctx) =>
                                `${ctx.dataset.label}: ${this.formatMoney(ctx.parsed.y)}`,
                        },
                    },
                },
                scales: {
                    x: { stacked: true, ticks: { maxRotation: 0, autoSkip: true } },
                    y: {
                        stacked: true,
                        beginAtZero: true,
                        ticks: { callback: (v) => this.formatMoney(v) },
                    },
                },
            },
        });
    }
}
