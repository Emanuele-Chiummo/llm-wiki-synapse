/**
 * SynapseMark — theme-aware inline SVG brand mark (S-mark, no squircle).
 *
 * Brand v1.0 rules:
 *  - Light surfaces: blue/gradient mark (diagonal BL→TR #1D4ED8 · #4338CA · #7C3AED).
 *  - Dark surfaces:  white-knockout mark (#FFFFFF), no gradient.
 *
 * The component reads the RESOLVED theme from settingsStore so it instantly
 * reflects manual theme toggles without a page reload. Using the store selector
 * (not document.documentElement.dataset.theme) keeps this reactive.
 *
 * Usage:
 *   <SynapseMark size={64} />          — auto (follows app theme)
 *   <SynapseMark size={64} variant="dark" />  — force white-knockout
 *   <SynapseMark size={64} variant="light" /> — force gradient
 */

import type { CSSProperties } from "react";
import { useSettingsStore, selectTheme, resolveTheme } from "../../store/settingsStore";
import { PRODUCT_IDENTITY } from "../../config/productIdentity";

interface SynapseMarkProps {
  /** Rendered width AND height (square mark). Defaults to 64px. */
  size?: number;
  /** Override theme resolution. 'auto' (default) tracks the Zustand store. */
  variant?: "auto" | "light" | "dark";
  className?: string;
  style?: CSSProperties;
}

export function SynapseMark({ size = 64, variant = "auto", className, style }: SynapseMarkProps) {
  const storedTheme = useSettingsStore(selectTheme);
  const resolved = variant === "auto" ? resolveTheme(storedTheme) : variant;
  const isDark = resolved === "dark";
  const isSimplified = size < 24;

  // exactOptionalPropertyTypes: only spread props that are actually defined
  const passThrough: { className?: string; style?: CSSProperties } = {};
  if (className !== undefined) passThrough.className = className;
  if (style !== undefined) passThrough.style = style;

  if (isSimplified) {
    return isDark ? (
      <SynapseMarkSimplifiedDark size={size} {...passThrough} />
    ) : (
      <SynapseMarkSimplifiedLight size={size} {...passThrough} />
    );
  }

  if (isDark) {
    return <SynapseMarkDark size={size} {...passThrough} />;
  }
  return <SynapseMarkLight size={size} {...passThrough} />;
}

/** White-knockout mark for dark surfaces. */
function SynapseMarkDark({
  size,
  className,
  style,
}: {
  size: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      viewBox="0 0 256 256"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      role="img"
      aria-label={PRODUCT_IDENTITY.displayName}
      data-mark-detail="master"
      className={className}
      style={style}
    >
      {/* Satellite edges */}
      <g data-mark-part="satellites">
        <path
          d="M186 62 L224 34"
          stroke="#ffffff"
          strokeOpacity={0.5}
          strokeWidth={6}
          strokeLinecap="round"
        />
        <path
          d="M70 194 L32 222"
          stroke="#ffffff"
          strokeOpacity={0.5}
          strokeWidth={6}
          strokeLinecap="round"
        />
        <path
          d="M128 128 L206 150"
          stroke="#ffffff"
          strokeOpacity={0.5}
          strokeWidth={6}
          strokeLinecap="round"
        />

        {/* Satellite nodes */}
        <circle cx={224} cy={34} r={10} fill="#ffffff" fillOpacity={0.85} />
        <circle cx={32} cy={222} r={10} fill="#ffffff" fillOpacity={0.85} />
        <circle cx={206} cy={150} r={8} fill="#ffffff" fillOpacity={0.85} />
      </g>

      {/* Main synaptic S-path */}
      <path
        d="M186 62 C 112 50 74 96 128 128 C 182 160 144 206 70 194"
        stroke="#ffffff"
        strokeWidth={22}
        strokeLinecap="round"
        fill="none"
      />

      {/* Core nodes */}
      <circle cx={186} cy={62} r={26} fill="#ffffff" />
      <circle cx={70} cy={194} r={26} fill="#ffffff" />
      <circle cx={128} cy={128} r={15} fill="#ffffff" />
    </svg>
  );
}

/** Gradient (blue/indigo/violet) mark for light surfaces. */
function SynapseMarkLight({
  size,
  className,
  style,
}: {
  size: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      viewBox="0 0 256 256"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      role="img"
      aria-label={PRODUCT_IDENTITY.displayName}
      data-mark-detail="master"
      className={className}
      style={style}
    >
      <defs>
        <linearGradient
          id="synMarkGradL"
          x1="40"
          y1="216"
          x2="216"
          y2="40"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#1D4ED8" />
          <stop offset="0.5" stopColor="#4338CA" />
          <stop offset="1" stopColor="#7C3AED" />
        </linearGradient>
        <linearGradient
          id="synMarkGradSoftL"
          x1="40"
          y1="216"
          x2="216"
          y2="40"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#1D4ED8" stopOpacity={0.35} />
          <stop offset="1" stopColor="#7C3AED" stopOpacity={0.35} />
        </linearGradient>
      </defs>

      {/* Satellite edges */}
      <g data-mark-part="satellites">
        <path
          d="M186 62 L224 34"
          stroke="url(#synMarkGradSoftL)"
          strokeWidth={6}
          strokeLinecap="round"
        />
        <path
          d="M70 194 L32 222"
          stroke="url(#synMarkGradSoftL)"
          strokeWidth={6}
          strokeLinecap="round"
        />
        <path
          d="M128 128 L206 150"
          stroke="url(#synMarkGradSoftL)"
          strokeWidth={6}
          strokeLinecap="round"
        />

        {/* Satellite nodes */}
        <circle cx={224} cy={34} r={10} fill="#7C3AED" />
        <circle cx={32} cy={222} r={10} fill="#1D4ED8" />
        <circle cx={206} cy={150} r={8} fill="#4338CA" />
      </g>

      {/* Main synaptic S-path */}
      <path
        d="M186 62 C 112 50 74 96 128 128 C 182 160 144 206 70 194"
        stroke="url(#synMarkGradL)"
        strokeWidth={22}
        strokeLinecap="round"
        fill="none"
      />

      {/* Core nodes */}
      <circle cx={186} cy={62} r={26} fill="url(#synMarkGradL)" />
      <circle cx={70} cy={194} r={26} fill="url(#synMarkGradL)" />
      <circle cx={128} cy={128} r={15} fill="url(#synMarkGradL)" />

      {/* Synaptic spark highlights */}
      <circle cx={186} cy={62} r={9} fill="#ffffff" fillOpacity={0.92} />
      <circle cx={70} cy={194} r={9} fill="#ffffff" fillOpacity={0.92} />
    </svg>
  );
}

/** Simplified favicon geometry for marks rendered below 24px. */
function SynapseMarkSimplifiedDark({
  size,
  className,
  style,
}: {
  size: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      role="img"
      aria-label={PRODUCT_IDENTITY.displayName}
      data-mark-detail="simplified"
      className={className}
      style={style}
    >
      <path
        d="M43 19 C 29 17 22 27 32 32 C 42 37 35 47 21 45"
        stroke="#ffffff"
        strokeWidth={5.5}
        strokeLinecap="round"
      />
      <circle cx={43} cy={19} r={5.5} fill="#ffffff" />
      <circle cx={21} cy={45} r={5.5} fill="#ffffff" />
      <circle cx={32} cy={32} r={3} fill="#ffffff" />
    </svg>
  );
}

/** Simplified gradient favicon geometry for light surfaces. */
function SynapseMarkSimplifiedLight({
  size,
  className,
  style,
}: {
  size: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      role="img"
      aria-label={PRODUCT_IDENTITY.displayName}
      data-mark-detail="simplified"
      className={className}
      style={style}
    >
      <defs>
        <linearGradient
          id="synMarkGradSimplifiedL"
          x1="0"
          y1="64"
          x2="64"
          y2="0"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#1D4ED8" />
          <stop offset="0.5" stopColor="#4338CA" />
          <stop offset="1" stopColor="#7C3AED" />
        </linearGradient>
      </defs>
      <path
        d="M43 19 C 29 17 22 27 32 32 C 42 37 35 47 21 45"
        stroke="url(#synMarkGradSimplifiedL)"
        strokeWidth={5.5}
        strokeLinecap="round"
      />
      <circle cx={43} cy={19} r={5.5} fill="url(#synMarkGradSimplifiedL)" />
      <circle cx={21} cy={45} r={5.5} fill="url(#synMarkGradSimplifiedL)" />
      <circle cx={32} cy={32} r={3} fill="url(#synMarkGradSimplifiedL)" />
    </svg>
  );
}
