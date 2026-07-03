/**
 * SectionErrorBoundary.tsx — per-section error boundary.
 *
 * Before this, an uncaught render error in any section (e.g. GraphViewer)
 * unmounted the ENTIRE React tree → white screen with no feedback. The
 * boundary contains the failure to the active section: the shell, nav and
 * other sections keep working, and the user gets a readable message with
 * a retry button. In dev the error message/stack is shown to aid debugging.
 */

import { Component, type ReactNode } from "react";

interface Props {
  /** Section identifier — resetting the key remounts the child. */
  sectionId: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class SectionErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error): void {
    // Dev aid: full stack in console (production: message only via UI).
    console.error(`[SectionErrorBoundary:${this.props.sectionId}]`, error);
  }

  override componentDidUpdate(prevProps: Props): void {
    // Switching section clears the error so other sections render normally.
    if (prevProps.sectionId !== this.props.sectionId && this.state.error !== null) {
      this.setState({ error: null });
    }
  }

  private readonly handleRetry = (): void => {
    this.setState({ error: null });
  };

  override render(): ReactNode {
    if (this.state.error !== null) {
      return (
        <div
          role="alert"
          data-testid="section-error-boundary"
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            height: "100%",
            gap: 12,
            padding: 24,
            color: "var(--syn-text)",
            textAlign: "center",
          }}
        >
          <span style={{ fontSize: 15, fontWeight: 700 }}>Qualcosa è andato storto in questa sezione</span>
          <code
            style={{
              fontSize: 12,
              color: "var(--syn-red)",
              maxWidth: 560,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {this.state.error.message}
            {__DEV__ ? `\n\n${(this.state.error.stack ?? "").split("\n").slice(0, 6).join("\n")}` : ""}
          </code>
          <button
            onClick={this.handleRetry}
            style={{
              padding: "8px 18px",
              borderRadius: 8,
              border: "1px solid var(--syn-border)",
              background: "var(--syn-surface)",
              color: "var(--syn-accent)",
              fontWeight: 650,
              cursor: "pointer",
            }}
          >
            Riprova
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
