/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { formatMonetary } from "@web/views/fields/formatters";

/**
 * Chart.js trend chart for the Billing Dashboard.
 *
 * Renders a stacked bar (paid + pending) per day plus a line for the
 * total invoiced amount. Chart.js itself is loaded on demand via
 * ``loadBundle("web.chartjs_lib")`` — same pattern used by Odoo's
 * graph view, gauge field and journal dashboard graph.
 */
export class BillingTrendChart extends Component {
    static template = "custom_billing_report.BillingTrendChart";
    static props = {
        trend: { type: Array },
        currencyId: { type: [Number, Boolean], optional: true },
    };

    setup() {
        this.canvasRef = useRef("canvas");
        onWillStart(() => loadBundle("web.chartjs_lib"));
        useEffect(
            () => {
                this._renderChart();
                return () => this._destroyChart();
            },
            () => [this.props.trend, this.props.currencyId]
        );
        onWillUnmount(() => this._destroyChart());
    }

    _destroyChart() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }

    _formatMoney(value) {
        if (this.props.currencyId) {
            return formatMonetary(value || 0, { currencyId: this.props.currencyId });
        }
        return Number(value || 0).toFixed(2);
    }

    _renderChart() {
        if (!this.canvasRef.el || typeof Chart === "undefined") {
            return;
        }
        this._destroyChart();

        const labels = this.props.trend.map((p) => p.date);
        const paid = this.props.trend.map((p) => p.paid);
        const pending = this.props.trend.map((p) => p.pending);
        const invoiced = this.props.trend.map((p) => p.invoiced);

        const ctx = this.canvasRef.el.getContext("2d");
        const formatTick = (v) => this._formatMoney(v);

        this.chart = new Chart(ctx, {
            type: "bar",
            data: {
                labels,
                datasets: [
                    {
                        type: "bar",
                        label: _t("Paid"),
                        data: paid,
                        backgroundColor: "rgba(25, 135, 84, 0.75)",
                        borderColor: "rgba(25, 135, 84, 1)",
                        borderWidth: 1,
                        stack: "billing",
                    },
                    {
                        type: "bar",
                        label: _t("Pending"),
                        data: pending,
                        backgroundColor: "rgba(255, 193, 7, 0.75)",
                        borderColor: "rgba(255, 193, 7, 1)",
                        borderWidth: 1,
                        stack: "billing",
                    },
                    {
                        type: "line",
                        label: _t("Invoiced"),
                        data: invoiced,
                        borderColor: "rgba(31, 78, 120, 1)",
                        backgroundColor: "rgba(31, 78, 120, 0.15)",
                        borderWidth: 2,
                        tension: 0.3,
                        pointRadius: 3,
                        fill: false,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { position: "bottom" },
                    tooltip: {
                        callbacks: {
                            label: (ctx) =>
                                `${ctx.dataset.label}: ${this._formatMoney(ctx.parsed.y)}`,
                        },
                    },
                },
                scales: {
                    x: { stacked: true },
                    y: {
                        stacked: true,
                        beginAtZero: true,
                        ticks: { callback: formatTick },
                    },
                },
            },
        });
    }
}
