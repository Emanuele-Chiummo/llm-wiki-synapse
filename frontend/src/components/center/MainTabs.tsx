/**
 * MainTabs.tsx — tab strip for the center panel (Graph | Chat).
 *
 * Phase 1 (v0.4): Graph tab is live; Chat tab is an aria-disabled stub.
 * WCAG 2.1 compliant tablist: arrow-key navigation, aria-selected, aria-disabled.
 *
 * INVARIANT I3: subscribes to graphStore via typed selectors only.
 */

import { useCallback, type KeyboardEvent } from "react";
import { useGraphStore } from "../../store/graphStore";
import { selectActiveTab, selectSetActiveTab } from "../../store/graphStore";
import type { CenterTab } from "../../store/graphStore";
import { GraphPanel } from "./GraphPanel";

interface Tab {
  id: CenterTab;
  label: string;
  disabled?: boolean;
}

const TABS: Tab[] = [
  { id: "graph", label: "Graph" },
  { id: "chat", label: "Chat", disabled: true },
];

export function MainTabs() {
  const activeTab = useGraphStore(selectActiveTab);
  const setActiveTab = useGraphStore(selectSetActiveTab);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      if (e.key === "ArrowRight") {
        const current = TABS.findIndex((t) => t.id === activeTab);
        for (let i = current + 1; i < TABS.length; i++) {
          const tab = TABS[i];
          if (tab && !tab.disabled) {
            setActiveTab(tab.id);
            break;
          }
        }
      } else if (e.key === "ArrowLeft") {
        const current = TABS.findIndex((t) => t.id === activeTab);
        for (let i = current - 1; i >= 0; i--) {
          const tab = TABS[i];
          if (tab && !tab.disabled) {
            setActiveTab(tab.id);
            break;
          }
        }
      }
    },
    [activeTab, setActiveTab],
  );

  return (
    <div
      className="main-tabs"
      style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}
    >
      {/* Tab strip */}
      <div
        role="tablist"
        aria-label="Center panel"
        className="main-tabs__strip"
        style={{
          display: "flex",
          gap: 0,
          borderBottom: "1px solid #21262d",
          background: "#161b22",
          flexShrink: 0,
        }}
        onKeyDown={handleKeyDown}
      >
        {TABS.map((tab) => {
          const isActive = tab.id === activeTab;
          const isDisabled = tab.disabled === true;

          return (
            <button
              key={tab.id}
              role="tab"
              id={`tab-${tab.id}`}
              aria-controls={`tabpanel-${tab.id}`}
              aria-selected={isActive}
              aria-disabled={isDisabled}
              disabled={isDisabled}
              tabIndex={isActive ? 0 : -1}
              className={`main-tabs__tab${isActive ? " main-tabs__tab--active" : ""}${isDisabled ? " main-tabs__tab--disabled" : ""}`}
              style={{
                padding: "8px 16px",
                border: "none",
                borderBottom: isActive ? "2px solid #58a6ff" : "2px solid transparent",
                background: "transparent",
                color: isActive ? "#e6edf3" : isDisabled ? "#484f58" : "#8b949e",
                fontSize: 13,
                fontWeight: isActive ? 600 : 400,
                cursor: isDisabled ? "not-allowed" : "pointer",
                opacity: isDisabled ? 0.5 : 1,
                transition: "color 0.1s ease, border-color 0.1s ease",
              }}
              onClick={() => {
                if (!isDisabled) setActiveTab(tab.id);
              }}
            >
              {tab.label}
              {isDisabled && (
                <span
                  aria-hidden="true"
                  style={{ marginLeft: 4, fontSize: 10, opacity: 0.6 }}
                >
                  (coming soon)
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Tab panels */}
      <div
        role="tabpanel"
        id="tabpanel-graph"
        aria-labelledby="tab-graph"
        hidden={activeTab !== "graph"}
        style={{ flex: 1, overflow: "hidden", display: activeTab === "graph" ? "flex" : "none", flexDirection: "column" }}
      >
        <GraphPanel />
      </div>

      <div
        role="tabpanel"
        id="tabpanel-chat"
        aria-labelledby="tab-chat"
        hidden={activeTab !== "chat"}
        style={{ flex: 1, display: activeTab === "chat" ? "flex" : "none", alignItems: "center", justifyContent: "center" }}
      >
        <p style={{ color: "#8b949e", fontSize: 14 }}>Chat panel coming in v0.4 Phase 2.</p>
      </div>
    </div>
  );
}
