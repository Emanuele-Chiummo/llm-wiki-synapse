/**
 * SectionSourceWatch.tsx — scheduled folder import (ADR-0020).
 * Extracted from SettingsPanel monolith (ADR-0055).
 */
import { useTranslation } from "react-i18next";
import { SectionHeader } from "../ui";
import { ImportScheduleCard } from "../ImportScheduleCard";

export function SectionSourceWatch() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionHeader title={t("settings.nav.sourceWatch")} desc={t("settings.import.title")} />
      <ImportScheduleCard />
    </div>
  );
}
