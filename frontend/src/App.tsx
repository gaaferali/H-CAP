import { useEffect, useMemo, useState } from "react";

import { api } from "./api/api";
import { Layout } from "./components/Layout";
import { TranslateTree, TranslationContext, translateArabic } from "./i18n";
import { Login } from "./pages";
import { renderRoute, roleRoutes, routeFromHash, routePaths } from "./routes";
import type { Locale, PlatformUser, Role, Route } from "./types/types";

export default function App() {
  const [user, setUser] = useState<PlatformUser | null>(null);
  const [locale, setLocale] = useState<Locale>("en");
  const [route, setRoute] = useState<Route>(routeFromHash);
  const translate = useMemo(
    () => (value: string) => (locale === "ar" ? translateArabic(value) : value),
    [locale],
  );

  useEffect(() => {
    const update = () => setRoute(routeFromHash());
    addEventListener("hashchange", update);
    if (localStorage.getItem("hcap_token")) api.me().then(setUser).catch(api.clearToken);
    return () => removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = locale === "ar" ? "rtl" : "ltr";
  }, [locale]);
  useEffect(() => {
    if (user && !roleRoutes[route].includes(user.role as Role)) location.hash = "#/dashboard";
  }, [route, user]);

  if (!user) {
    return (
      <TranslationContext.Provider value={translate}>
        <Login onLogin={(staff) => { setUser(staff); location.hash = "#/dashboard"; }} />
      </TranslationContext.Provider>
    );
  }

  const go = (target: Route) => { location.hash = `#/${routePaths[target]}`; };
  const signOut = async () => {
    try { await api.logout(); } finally { api.clearToken(); setUser(null); }
  };
  return (
    <TranslationContext.Provider value={translate}>
      <TranslateTree>
        <Layout
          locale={locale}
          route={route}
          user={user}
          translate={translate}
          onNavigate={go}
          onLocaleChange={() => setLocale(locale === "en" ? "ar" : "en")}
          onSignOut={() => void signOut()}
        >
          {renderRoute(route, user, setUser)}
        </Layout>
      </TranslateTree>
    </TranslationContext.Provider>
  );
}
