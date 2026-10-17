import { Languages, UserCog } from "lucide-react";
import type { ReactNode } from "react";

import { nav, roleRoutes } from "../routes";
import type { Locale, PlatformUser, Role, Route } from "../types/types";

type LayoutProps = {
  children: ReactNode;
  locale: Locale;
  route: Route;
  user: PlatformUser;
  translate: (value: string) => string;
  onNavigate: (route: Route) => void;
  onLocaleChange: () => void;
  onSignOut: () => void;
};

const secondaryRouteTitles: Partial<Record<Route, string>> = {
  medical_entitlements: "Medical entitlements",
  medical_distribution: "Medical distribution",
  profile: "Profile settings",
};

// Shows the page header, navigation, language control, and signed-in user details.
export function Layout({
  children, locale, route, user, translate, onNavigate, onLocaleChange, onSignOut,
}: LayoutProps) {
  const role = user.role as Role;
  const routeTitle = nav.find(([item]) => item === route)?.[1]
    ?? secondaryRouteTitles[route]
    ?? "Dashboard";
  const title = translate(routeTitle);
  const platformName = translate("Humanitarian Cash Assistance Platform");
  const subtitle = translate("Secure, accountable assistance delivery");

  return (
    <div dir={locale === "ar" ? "rtl" : "ltr"}>
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className="sidebar">
        <div className="agency-lockup">
          <div className="agency-mark" aria-hidden="true">+</div>
          <div><strong>H-CAP</strong><small>Humanitarian operations</small></div>
        </div>
        <nav aria-label={translate("Primary navigation")}>
          {nav.filter(([item]) => roleRoutes[item].includes(role)).map(([item, label, Icon]) => (
            <button className={`nav-item ${route === item ? "active" : ""}`} key={item} onClick={() => onNavigate(item)}>
              <Icon size={17} /> {translate(label)}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer"><strong>{user.full_name}</strong><small>{translate(user.role)}</small></div>
      </aside>
      <main className="workspace" id="main">
        <header className="topbar">
          <div className="topbar-copy">
            <p className="eyebrow">{platformName}</p>
            <h1>{title}</h1>
            <p className="topbar-description">{subtitle}</p>
          </div>
          <div className="topbar-actions">
            <button className="btn btn-outline-primary btn-sm" onClick={() => onNavigate("profile")}>
              <UserCog size={16} /> {translate("Edit profile")}
            </button>
            <button className="btn btn-outline-primary btn-sm" onClick={onLocaleChange}>
              <Languages size={16} /> {locale === "en" ? "العربية" : "English"}
            </button>
            <button className="btn btn-outline-primary btn-sm" onClick={onSignOut}>{translate("Sign out")}</button>
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}
