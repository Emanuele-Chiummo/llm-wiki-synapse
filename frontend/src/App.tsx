/**
 * App.tsx — Synapse v0.5 root component.
 *
 * Renders the 3-panel AppShell (F1 / ADR-0017).
 * All layout, panel management, and routing is delegated to AppShell.
 */

import React from "react";
import { AppShell } from "./components/AppShell";

const App: React.FC = () => <AppShell />;

export default App;
