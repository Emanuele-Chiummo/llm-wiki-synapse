/**
 * ScenarioTemplates.tsx — quick-action buttons for common usage patterns.
 *
 * Phase 1 (v0.4): two template buttons that select a representative node in the
 * graph store (first concept, first entity) so the user can demo graph ↔ tree
 * ↔ preview sync without manual clicking.
 *
 * INVARIANT I3: reads nodes via typed selector + useShallow.
 */

import { useCallback } from "react";
import { useShallow } from "zustand/react/shallow";
import { useGraphStore } from "../../store/graphStore";
import { selectNodes, selectSelectPage } from "../../store/graphStore";

export function ScenarioTemplates() {
  const nodes = useGraphStore(useShallow(selectNodes));
  const selectPage = useGraphStore(selectSelectPage);

  /** Find the first node whose type matches, or null. */
  const findFirst = useCallback(
    (type: string) => nodes.find((n) => n.type === type) ?? null,
    [nodes],
  );

  const handleExploreHighDegree = useCallback(() => {
    // Find the node with the highest degree (proxy: use degree field if present,
    // else fall back to first concept).
    const sorted = [...nodes].sort((a, b) => {
      const da = a.degree ?? 0;
      const db = b.degree ?? 0;
      return db - da;
    });
    const top = sorted[0];
    if (top) selectPage(top.id, "tree");
  }, [nodes, selectPage]);

  const handleExploreConcept = useCallback(() => {
    const node = findFirst("concept");
    if (node) selectPage(node.id, "tree");
  }, [findFirst, selectPage]);

  if (nodes.length === 0) return null;

  return (
    <div
      className="scenario-templates"
      aria-label="Scenario templates"
      data-testid="scenario-templates"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "8px 8px",
        borderBottom: "1px solid var(--syn-border)",
      }}
    >
      <span
        style={{
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: "0.05em",
          textTransform: "uppercase",
          color: "var(--syn-text-dim)",
          padding: "0 4px",
        }}
      >
        Quick Start
      </span>

      <TemplateButton
        label="Most connected node"
        description="Jump to the highest-degree node in the graph"
        onClick={handleExploreHighDegree}
      />

      <TemplateButton
        label="First concept"
        description="Navigate to the first concept-type page"
        onClick={handleExploreConcept}
      />
    </div>
  );
}

// ─── Sub-component ────────────────────────────────────────────────────────────

interface TemplateButtonProps {
  label: string;
  description: string;
  onClick: () => void;
}

function TemplateButton({ label, description, onClick }: TemplateButtonProps) {
  return (
    <button
      className="scenario-templates__btn"
      aria-label={`${label}: ${description}`}
      title={description}
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        padding: "5px 8px",
        border: "1px solid var(--syn-border)",
        borderRadius: 6,
        background: "var(--syn-surface)",
        cursor: "pointer",
        textAlign: "left",
        color: "var(--syn-text-muted)",
        fontSize: 12,
        transition: "background 0.1s ease, border-color 0.1s ease",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-accent-soft)";
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-accent)";
        (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-accent)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-surface)";
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-border)";
        (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-text-muted)";
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: "var(--syn-accent)",
          flexShrink: 0,
        }}
      />
      <span
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          flex: 1,
        }}
      >
        {label}
      </span>
    </button>
  );
}
