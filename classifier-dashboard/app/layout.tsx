import type { Metadata } from "next";
import type { ReactNode } from "react";
import { colors, mono } from "./lib/theme";

export const metadata: Metadata = {
  title: "Vigil — MNQ Pre-Market Regime",
  description:
    "Forward-looking pre-market intelligence for MNQ futures. Regime classification and GREEN/YELLOW/RED verdict before the open.",
};

const navLink = {
  color: colors.textMuted,
  textDecoration: "none",
  fontFamily: mono,
  fontSize: 12,
  letterSpacing: 2,
  textTransform: "uppercase" as const,
  padding: "6px 10px",
  border: `1px solid ${colors.border}`,
  borderRadius: 2,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          padding: 0,
          background: colors.bg,
          color: colors.text,
          fontFamily: mono,
          fontSize: 14,
          minHeight: "100vh",
          WebkitFontSmoothing: "antialiased",
        }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderBottom: `1px solid ${colors.border}`,
            padding: "14px 28px",
            background: colors.bg,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 14,
            }}
          >
            <span
              style={{
                fontFamily: mono,
                fontSize: 18,
                letterSpacing: 4,
                color: colors.text,
              }}
            >
              VIGIL
            </span>
            <span
              style={{
                fontFamily: mono,
                fontSize: 11,
                letterSpacing: 2,
                color: colors.textDim,
                textTransform: "uppercase",
              }}
            >
              MNQ Pre-Market Regime
            </span>
          </div>
          <nav style={{ display: "flex", gap: 10 }}>
            <a href="/" style={navLink}>
              Today
            </a>
            <a href="/history" style={navLink}>
              History
            </a>
          </nav>
        </header>
        <main style={{ padding: "28px", maxWidth: 1400, margin: "0 auto" }}>
          {children}
        </main>
      </body>
    </html>
  );
}
