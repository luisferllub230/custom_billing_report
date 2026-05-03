/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { formatMonetary } from "@web/views/fields/formatters";

export class BillingTopCreatorsChart extends Component {
    static template = "custom_billing_report.BillingTopCreatorsChart";
    static props = {
        topCreators: { type: Array },
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
            () => [this.props.topCreators, this.props.currencyId]
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

        const top = (this.props.topCreators || []).slice(0, 8);
        const labels = top.map((u) => u.user_name);
        const paid = top.map((u) => u.amount_paid || 0);
        const pending = top.map((u) => u.amount_pending || 0);

        const ctx = this.canvasRef.el.getContext("2d");
        this.chart = new Chart(ctx, {
            type: "bar",
            data: {
                labels,
                datasets: [
                    {
                        label: _t("Paid"),
                        data: paid,
                        backgroundColor: "rgba(102, 16, 242, 0.85)",
                        stack: "total",
                    },
                    {
                        label: _t("Pending"),
                        data: pending,
                        backgroundColor: "rgba(255, 193, 7, 0.85)",
                        stack: "total",
                    },
                ],
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: "bottom" },
                    tooltip: {
                        callbacks: {
                            label: (ctx) =>
                                `${ctx.dataset.label}: ${this._formatMoney(ctx.parsed.x)}`,
                        },
                    },
                },
                scales: {
                    x: {
                        stacked: true,
                        beginAtZero: true,
                        ticks: { callback: (v) => this._formatMoney(v) },
                    },
                    y: { stacked: true },
                },
            },
        });
    }
}
