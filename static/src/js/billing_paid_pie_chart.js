/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { formatMonetary } from "@web/views/fields/formatters";

export class BillingPaidPieChart extends Component {
    static template = "custom_billing_report.BillingPaidPieChart";
    static props = {
        topCustomers: { type: Array },
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
            () => [this.props.topCustomers, this.props.currencyId]
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

    get counts() {
        let paid = 0;
        let pending = 0;
        for (const cust of this.props.topCustomers || []) {
            if ((cust.amount_pending || 0) <= 0.005) {
                paid += 1;
            } else {
                pending += 1;
            }
        }
        return { paid, pending };
    }

    _renderChart() {
        if (!this.canvasRef.el || typeof Chart === "undefined") {
            return;
        }
        this._destroyChart();
        const { paid, pending } = this.counts;
        const ctx = this.canvasRef.el.getContext("2d");
        this.chart = new Chart(ctx, {
            type: "pie",
            data: {
                labels: [_t("Paid customers"), _t("Pending customers")],
                datasets: [{
                    data: [paid, pending],
                    backgroundColor: [
                        "rgba(25, 135, 84, 0.85)",
                        "rgba(255, 193, 7, 0.85)",
                    ],
                    borderColor: ["#ffffff", "#ffffff"],
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
                                `${ctx.label}: ${ctx.parsed}`,
                        },
                    },
                },
            },
        });
    }
}
