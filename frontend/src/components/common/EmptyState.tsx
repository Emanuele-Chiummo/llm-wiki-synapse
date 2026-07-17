import type { ReactNode } from "react";

interface EmptyStateAction {
  label: string;
  onClick: () => void;
  variant?: "primary" | "secondary";
}

interface EmptyStateProps {
  eyebrow?: string;
  title: string;
  body?: string;
  children?: ReactNode;
  actions?: EmptyStateAction[];
  testId?: string;
}

export function EmptyState({
  eyebrow,
  title,
  body,
  children,
  actions = [],
  testId,
}: EmptyStateProps) {
  return (
    <div className="syn-empty-state" data-testid={testId}>
      {eyebrow && <div className="syn-empty-state__eyebrow">{eyebrow}</div>}
      <div className="syn-empty-state__title">{title}</div>
      {body && <p className="syn-empty-state__body">{body}</p>}
      {children && <div className="syn-empty-state__content">{children}</div>}
      {actions.length > 0 && (
        <div className="syn-empty-state__actions">
          {actions.map((action) => (
            <button
              key={action.label}
              type="button"
              className={
                action.variant === "primary" ? "syn-btn syn-btn--primary" : "syn-btn syn-btn--secondary"
              }
              onClick={action.onClick}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

interface SectionNoticeProps {
  children: ReactNode;
  tone?: "info" | "success" | "warning" | "danger";
  role?: "status" | "alert";
}

export function SectionNotice({ children, tone = "info", role = "status" }: SectionNoticeProps) {
  return (
    <div className={`syn-section-notice syn-section-notice--${tone}`} role={role}>
      {children}
    </div>
  );
}
