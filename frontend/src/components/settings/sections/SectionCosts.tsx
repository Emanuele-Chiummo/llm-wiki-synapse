/**
 * SectionCosts.tsx — inference cost tracking (R9-1).
 * Extracted from SettingsPanel monolith (ADR-0055).
 * I3: single fetch on mount + manual Refresh; no background polling; local state only.
 */
import React, { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { SectionHeader } from "../ui";
import { Button } from "../../ui/Button";
import { fetchCostsSummary, type CostsSummary } from "../../../api/costsClient";

// LLM Wiki card style — bordered surface card (brand colors only, never black).
const COST_CARD: React.CSSProperties = {
  border: "1px solid var(--syn-border)",
  borderRadius: 10,
  background: "var(--syn-surface)",
  padding: "14px 16px",
};

export function SectionCosts() {
  const { t } = useTranslation();
  const [data, setData] = useState<CostsSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(false);

  const [month, setMonth] = useState<string>(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  });

  const load = useCallback(async (selectedMonth: string) => {
    setLoading(true);
    setErr(false);
    try {
      const result = await fetchCostsSummary(selectedMonth);
      setData(result);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
      setErr(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(month);
  }, [load, month]);

  const handleMonthChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setMonth(e.target.value);
  };

  const renderDayBars = (days: CostsSummary["by_day"]) => {
    if (days.length === 0) return null;
    const max = Math.max(...days.map((d) => d.total_usd), 0.0001);
    const BAR_W = 6;
    const GAP = 2;
    const H = 36;
    const totalW = days.length * (BAR_W + GAP);

    return (
      <svg
        width={totalW}
        height={H + 16}
        data-testid="costs-day-chart"
        aria-label={t("settings.costs.byDay")}
        role="img"
        style={{ display: "block", overflow: "visible" }}
      >
        {days.map((d, i) => {
          const barH = Math.max(2, Math.round((d.total_usd / max) * H));
          const x = i * (BAR_W + GAP);
          const y = H - barH;
          return (
            <g key={d.date}>
              <title>{`${d.date}: $${d.total_usd.toFixed(4)}`}</title>
              <rect
                x={x}
                y={y}
                width={BAR_W}
                height={barH}
                fill="var(--syn-accent)"
                opacity={0.8}
                rx={1}
              />
            </g>
          );
        })}
      </svg>
    );
  };

  return (
    <div>
      <SectionHeader title={t("settings.costs.title")} desc={t("settings.costs.desc")} />

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20 }}>
        <label style={{ fontSize: 12, color: "var(--syn-text-muted)", flexShrink: 0 }}>
          {t("settings.costs.period")}
        </label>
        <input
          type="month"
          data-testid="costs-month-selector"
          value={month}
          onChange={handleMonthChange}
          className="syn-input"
          style={{ width: 160 }}
        />
        <Button
          variant="accent-ghost"
          data-testid="costs-refresh-btn"
          onClick={() => {
            void load(month);
          }}
          disabled={loading}
        >
          {loading ? "…" : t("settings.costs.refresh")}
        </Button>
      </div>

      {err && (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>
          {t("settings.costs.error")}
        </p>
      )}
      {loading && !data && (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>
          {t("settings.costs.loading")}
        </p>
      )}

      {data !== null && (
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div style={COST_CARD}>
            {data.threshold_alert && (
              <div
                data-testid="costs-threshold-alert"
                style={{
                  marginBottom: 12,
                  padding: "8px 12px",
                  background: "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)",
                  border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                  borderRadius: 6,
                  fontSize: 12,
                  color: "var(--syn-red)",
                  fontWeight: 600,
                }}
                role="alert"
              >
                {t("settings.costs.thresholdAlert", { threshold: data.threshold_usd.toFixed(2) })}
              </div>
            )}
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span
                style={{
                  fontSize: 28,
                  fontWeight: 700,
                  color: "var(--syn-text)",
                  fontFamily: "var(--syn-font-mono)",
                }}
                data-testid="costs-monthly-total"
              >
                ${data.monthly_total_usd.toFixed(4)}
              </span>
              <span style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>
                {t("settings.costs.monthlyTotal")}
              </span>
            </div>
          </div>

          {data.by_day.length > 0 && (
            <div style={COST_CARD}>
              <p
                style={{
                  margin: "0 0 8px",
                  fontSize: 12,
                  fontWeight: 600,
                  color: "var(--syn-text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                }}
              >
                {t("settings.costs.byDay")}
              </p>
              <div style={{ overflowX: "auto" }}>{renderDayBars(data.by_day)}</div>
            </div>
          )}

          {data.by_provider.length > 0 && (
            <div style={COST_CARD}>
              <p
                style={{
                  margin: "0 0 8px",
                  fontSize: 12,
                  fontWeight: 600,
                  color: "var(--syn-text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                }}
              >
                {t("settings.costs.byProvider")}
              </p>
              {data.by_provider_note && (
                <p
                  style={{
                    fontSize: 11,
                    color: "var(--syn-text-dim)",
                    margin: "0 0 8px",
                    lineHeight: 1.5,
                  }}
                >
                  {data.by_provider_note}
                </p>
              )}
              <table
                style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}
                data-testid="costs-by-provider"
              >
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--syn-border)" }}>
                    <th
                      style={{
                        padding: "4px 0",
                        textAlign: "left",
                        color: "var(--syn-text-muted)",
                        fontWeight: 600,
                      }}
                    >
                      {t("settings.costs.providerCol")}
                    </th>
                    <th
                      style={{
                        padding: "4px 0",
                        textAlign: "right",
                        color: "var(--syn-text-muted)",
                        fontWeight: 600,
                      }}
                    >
                      {t("settings.costs.totalUsd")}
                    </th>
                    <th
                      style={{
                        padding: "4px 0",
                        textAlign: "right",
                        color: "var(--syn-text-muted)",
                        fontWeight: 600,
                      }}
                    >
                      {t("settings.costs.callCount")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_provider.map((row) => (
                    <tr key={row.provider} style={{ borderBottom: "1px solid var(--syn-border)" }}>
                      <td
                        style={{
                          padding: "6px 0",
                          color: "var(--syn-text)",
                          fontFamily: "var(--syn-font-mono)",
                        }}
                      >
                        {row.provider}
                      </td>
                      <td
                        style={{
                          padding: "6px 0",
                          textAlign: "right",
                          color: "var(--syn-text)",
                          fontFamily: "var(--syn-font-mono)",
                        }}
                      >
                        ${row.total_usd.toFixed(4)}
                      </td>
                      <td
                        style={{
                          padding: "6px 0",
                          textAlign: "right",
                          color: "var(--syn-text-muted)",
                        }}
                      >
                        {row.call_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data.by_operation.length > 0 && (
            <div style={COST_CARD}>
              <p
                style={{
                  margin: "0 0 8px",
                  fontSize: 12,
                  fontWeight: 600,
                  color: "var(--syn-text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                }}
              >
                {t("settings.costs.byOperation")}
              </p>
              <table
                style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}
                data-testid="costs-by-operation"
              >
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--syn-border)" }}>
                    <th
                      style={{
                        padding: "4px 0",
                        textAlign: "left",
                        color: "var(--syn-text-muted)",
                        fontWeight: 600,
                      }}
                    >
                      {t("settings.costs.operationCol")}
                    </th>
                    <th
                      style={{
                        padding: "4px 0",
                        textAlign: "right",
                        color: "var(--syn-text-muted)",
                        fontWeight: 600,
                      }}
                    >
                      {t("settings.costs.totalUsd")}
                    </th>
                    <th
                      style={{
                        padding: "4px 0",
                        textAlign: "right",
                        color: "var(--syn-text-muted)",
                        fontWeight: 600,
                      }}
                    >
                      {t("settings.costs.callCount")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_operation.map((row) => (
                    <tr key={row.operation} style={{ borderBottom: "1px solid var(--syn-border)" }}>
                      <td
                        style={{
                          padding: "6px 0",
                          color: "var(--syn-text)",
                          fontFamily: "var(--syn-font-mono)",
                        }}
                      >
                        {row.operation}
                      </td>
                      <td
                        style={{
                          padding: "6px 0",
                          textAlign: "right",
                          color: "var(--syn-text)",
                          fontFamily: "var(--syn-font-mono)",
                        }}
                      >
                        ${row.total_usd.toFixed(4)}
                      </td>
                      <td
                        style={{
                          padding: "6px 0",
                          textAlign: "right",
                          color: "var(--syn-text-muted)",
                        }}
                      >
                        {row.call_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data.by_provider.length === 0 && data.by_operation.length === 0 && (
            <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>
              {t("settings.costs.noData")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
