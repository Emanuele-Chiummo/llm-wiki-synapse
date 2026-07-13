import { WifiOff } from "lucide-react";
import { useTranslation } from "react-i18next";

import { selectBackendConnectionState, useStatusStore } from "../../store/statusStore";
import { openSetupWizard } from "../setup/setupEvents";

export function BackendConnectionBanner() {
  const { t } = useTranslation();
  const connectionState = useStatusStore(selectBackendConnectionState);

  if (connectionState !== "offline") return null;

  const openConnectionCheck = () => {
    openSetupWizard(1);
  };

  return (
    <div className="syn-connection-banner" data-testid="backend-connection-banner" role="status">
      <WifiOff size={15} aria-hidden="true" />
      <div className="syn-connection-banner__copy">
        <strong>{t("connection.offlineTitle")}</strong>
        <span>{t("connection.offlineBody")}</span>
      </div>
      <button
        type="button"
        className="syn-btn syn-btn--secondary syn-btn--sm"
        onClick={openConnectionCheck}
      >
        {t("connection.checkSetup")}
      </button>
    </div>
  );
}
