/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { formatMonetary } from "@web/views/fields/formatters";

/**
 * Per-payment-category trend (one chart per bucket: cash, card,
 * transfer, bank). Renders a small filled-area line chart so each
 * bucket can be inspected in isolation — that's what the cashier
 * uses when drilling into a single payment type during the daily
 * cash-up ("cuadre diario").
 */
export class BillingPaymentCategoryChart extends Component {
    static template = "custom_billing_report.BillingPaymentCategoryChart";
    static props = {
        category: { type: String },
        label: { type: String },
        color: { type: String },
        total: { type: Number, optional: true },
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
            () => [this.props.trend, this.props.category, this.props.currencyId]
        );
        onWillUnmount(() => this._destroyChart());
    }

    _destroyChart() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }

    formatMoney(value) {
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

        const trend = this.props.trend || [];
        const labels = trend.map((p) => p.date);
        const data = trend.map((p) => p[this.props.category] || 0);
        const ctx = this.canvasRef.el.getContext("2d");
        this.chart = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: [{
                    label: this.props.label,
                    data,
                    borderColor: this.props.color,
                    backgroundColor: this.props.color + "33",
                    fill: true,
                    tension: 0.25,
                    pointRadius: 2,
                    borderWidth: 2,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (ctx) =>
                                `${ctx.dataset.label}: ${this.formatMoney(ctx.parsed.y)}`,
                        },
                    },
                },
                scales: {
                    x: { ticks: { maxRotation: 0, autoSkip: true } },
                    y: {
                        beginAtZero: true,
                        ticks: { callback: (v) => this.formatMoney(v) },
                    },
                },
            },
        });
    }
}
