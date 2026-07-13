import { ArrowRight, BookOpen, FolderKanban, Network, Settings, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";

interface HomeGettingStartedProps {
  backendReady: boolean;
  providerReady: boolean;
  workspaceName: string;
  onImport: () => void;
  onConfigureProvider: () => void;
  onOpenProjects: () => void;
}

export function HomeGettingStarted({
  backendReady,
  providerReady,
  workspaceName,
  onImport,
  onConfigureProvider,
  onOpenProjects,
}: HomeGettingStartedProps) {
  const { t } = useTranslation();

  return (
    <section className="home-getting-started" data-testid="home-getting-started">
      <div className="home-getting-started__intro">
        <span className="home-getting-started__eyebrow">{t("home.gettingStarted.eyebrow")}</span>
        <h1>{t("home.gettingStarted.title")}</h1>
        <p>{t("home.gettingStarted.body")}</p>
      </div>

      <div className="home-getting-started__flow" aria-label={t("home.gettingStarted.flowLabel")}>
        <div className="home-getting-started__step home-getting-started__step--active">
          <Upload size={18} aria-hidden="true" />
          <strong>{t("home.gettingStarted.sourcesTitle")}</strong>
          <span>{t("home.gettingStarted.sourcesBody")}</span>
        </div>
        <ArrowRight className="home-getting-started__arrow" size={18} aria-hidden="true" />
        <div className="home-getting-started__step">
          <Network size={18} aria-hidden="true" />
          <strong>{t("home.gettingStarted.connectionsTitle")}</strong>
          <span>{t("home.gettingStarted.connectionsBody")}</span>
        </div>
        <ArrowRight className="home-getting-started__arrow" size={18} aria-hidden="true" />
        <div className="home-getting-started__step">
          <BookOpen size={18} aria-hidden="true" />
          <strong>{t("home.gettingStarted.wikiTitle")}</strong>
          <span>{t("home.gettingStarted.wikiBody")}</span>
        </div>
      </div>

      <div className="home-getting-started__readiness">
        <div>
          <span
            className={`home-getting-started__status${backendReady ? " home-getting-started__status--ready" : ""}`}
          />
          <span>
            {backendReady
              ? t("home.gettingStarted.backendReady")
              : t("home.gettingStarted.backendNeeded")}
          </span>
        </div>
        <div>
          <span
            className={`home-getting-started__status${providerReady ? " home-getting-started__status--ready" : ""}`}
          />
          <span>
            {providerReady
              ? t("home.gettingStarted.providerReady")
              : t("home.gettingStarted.providerNeeded")}
          </span>
        </div>
        <div>
          <FolderKanban size={13} aria-hidden="true" />
          <span>{t("home.gettingStarted.workspace", { name: workspaceName })}</span>
        </div>
      </div>

      <div className="home-getting-started__actions">
        <button
          type="button"
          className="syn-btn syn-btn--primary"
          data-testid="home-getting-started-import"
          onClick={onImport}
        >
          <Upload size={14} aria-hidden="true" />
          {t("home.gettingStarted.importCta")}
        </button>
        {!providerReady && (
          <button
            type="button"
            className="syn-btn syn-btn--secondary"
            onClick={onConfigureProvider}
          >
            <Settings size={14} aria-hidden="true" />
            {t("home.gettingStarted.providerCta")}
          </button>
        )}
        <button type="button" className="syn-btn syn-btn--ghost" onClick={onOpenProjects}>
          {t("home.gettingStarted.projectsCta")}
        </button>
      </div>
    </section>
  );
}
