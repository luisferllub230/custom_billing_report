/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { formatMonetary } from "@web/views/fields/formatters";

export class BillingHealthGauge extends Component {
    static template = "custom_billing_report.BillingHealthGauge";
    static props = {
        health: { type: Object },
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
            () => [this.props.health, this.props.currencyId]
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

    get statusClass() {
        return `o_billing_health_${this.props.health.status || "neutral"}`;
    }

    get ratioPercent() {
        return Math.round((this.props.health.ratio || 0) * 100);
    }

    _renderChart() {
        if (!this.canvasRef.el || typeof Chart === "undefined") {
            return;
        }
        this._destroyChart();

        const { paid_amount, pending_amount, status } = this.props.health;
        const palette = {
            good:    { fill: "rgba(25, 135, 84, 0.85)",  ring: "rgba(25, 135, 84, 0.2)"  },
            normal:  { fill: "rgba(255, 193, 7, 0.85)",  ring: "rgba(255, 193, 7, 0.2)"  },
            bad:     { fill: "rgba(220, 53, 69, 0.85)",  ring: "rgba(220, 53, 69, 0.2)"  },
        };
        const colors = palette[status] || palette.normal;

        const ctx = this.canvasRef.el.getContext("2d");
        this.chart = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels: [_t("Paid"), _t("Pending")],
                datasets: [{
                    data: [paid_amount || 0, pending_amount || 0],
                    backgroundColor: [colors.fill, colors.ring],
                    borderColor: ["#ffffff", "#ffffff"],
                    borderWidth: 2,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "65%",
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
}
