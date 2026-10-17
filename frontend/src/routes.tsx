import {
  Banknote, BarChart3, Bot, ClipboardCheck, FileUp, Globe2,
  HeartPulse, LayoutDashboard, MessageSquareWarning, PackageSearch, ShieldCheck, Stethoscope, UserCog, Users,
} from "lucide-react";
import type { Dispatch, SetStateAction } from "react";

import type { PlatformUser, Role, Route } from "./types/types";
import {
  ActivitiesPage, AssistancePage, AuditPage, AutomationPage,
  ComplaintsPage, Dashboard, DeliveryReviewPage, DistributionEventsPage,
  EligibilityPage, EnrollmentModalityPage, EntitlementsPage, ImportsPage,
  Intake, PaymentsPage, PdmPage, ProfilePage, ProgramsPage, TenantsPage,
  UsersPage,
  MedicalPage,
} from "./pages";

export const roleRoutes: Record<Route, Role[]> = {
  dashboard: ["ADMIN", "FIELD_OFFICER", "FINANCE", "REVIEWER", "SUPPORT", "MANAGER", "AUDITOR"],
  tenants: ["ADMIN"],
  programs: ["ADMIN", "MANAGER", "FINANCE"],
  households: ["ADMIN", "FIELD_OFFICER", "MANAGER", "FINANCE"],
  beneficiaries: ["ADMIN", "FIELD_OFFICER", "REVIEWER", "MANAGER", "FINANCE"],
  eligibility: ["ADMIN", "REVIEWER", "MANAGER"],
  enrollment: ["ADMIN", "REVIEWER", "MANAGER", "FINANCE"],
  payments: ["ADMIN", "FINANCE", "MANAGER", "AUDITOR"],
  pdm: ["ADMIN", "SUPPORT", "FIELD_OFFICER", "MANAGER", "AUDITOR"],
  complaints: ["ADMIN", "SUPPORT", "MANAGER"],
  activities: ["ADMIN", "MANAGER", "FIELD_OFFICER", "REVIEWER", "FINANCE", "SUPPORT", "AUDITOR"],
  audit: ["ADMIN", "MANAGER", "AUDITOR"],
  imports: ["ADMIN", "FIELD_OFFICER", "MANAGER"],
  automation: ["ADMIN", "MANAGER", "REVIEWER", "FINANCE", "AUDITOR"],
  assistance: ["ADMIN", "MANAGER", "REVIEWER", "AUDITOR"],
  nfi_entitlements: ["ADMIN", "MANAGER", "FINANCE", "REVIEWER", "AUDITOR"],
  distribution_events: ["ADMIN", "MANAGER", "REVIEWER", "FINANCE", "AUDITOR"],
  delivery_review: ["ADMIN", "MANAGER", "REVIEWER", "FINANCE", "AUDITOR"],
  medical: ["ADMIN", "MANAGER", "REVIEWER", "FINANCE", "AUDITOR"],
  medical_inventory: ["ADMIN", "MANAGER", "REVIEWER", "FINANCE", "AUDITOR"],
  medical_entitlements: ["ADMIN", "MANAGER", "REVIEWER", "FINANCE", "AUDITOR"],
  medical_distribution: ["ADMIN", "MANAGER", "REVIEWER", "FINANCE", "AUDITOR"],
  medical_equipment: ["ADMIN", "MANAGER", "REVIEWER", "FINANCE", "AUDITOR"],
  users: ["ADMIN", "MANAGER"],
  profile: ["ADMIN", "FIELD_OFFICER", "FINANCE", "REVIEWER", "SUPPORT", "MANAGER", "AUDITOR"],
};

export const nav: Array<[Route, string, typeof LayoutDashboard]> = [
  ["dashboard", "Dashboard", LayoutDashboard],
  ["tenants", "Platform administration", Globe2],
  ["programs", "Programs", Globe2],
  ["households", "Households", Users],
  ["beneficiaries", "Beneficiaries", Users],
  ["eligibility", "Eligibility review", ClipboardCheck],
  ["enrollment", "Enrollment decisions", ClipboardCheck],
  ["payments", "Payments", Banknote],
  ["pdm", "PDM", BarChart3],
  ["complaints", "Complaints", MessageSquareWarning],
  ["activities", "Program activities", ClipboardCheck],
  ["audit", "Audit", ShieldCheck],
  ["imports", "Import records", FileUp],
  ["automation", "AI & Automation", Bot],
  ["assistance", "Assistance delivery", Banknote],
  ["nfi_entitlements", "Entitlement", ClipboardCheck],
  ["distribution_events", "Distribution Events", ClipboardCheck],
  ["delivery_review", "Delivery Review", ClipboardCheck],
  ["medical", "Medical Assistance", HeartPulse],
  ["medical_inventory", "Medical inventory", PackageSearch],
  ["medical_equipment", "Medical equipment", Stethoscope],
  ["users", "User administration", UserCog],
];

export const routePaths: Record<Route, string> = {
  dashboard: "dashboard", tenants: "tenants", programs: "programs",
  households: "intake/households", beneficiaries: "intake/beneficiaries",
  eligibility: "eligibility", enrollment: "enrollment", payments: "payments",
  pdm: "pdm", complaints: "complaints", activities: "activities",
  audit: "audit", imports: "imports", automation: "automation", assistance: "assistance",
  nfi_entitlements: "entitlements", distribution_events: "distribution-events",
  delivery_review: "delivery-review", users: "users", profile: "profile",
  medical: "medical", medical_inventory: "medical/inventory",
  medical_entitlements: "medical/entitlements", medical_distribution: "medical/distribution",
  medical_equipment: "medical/equipment",
};

export const routeFromHash = (): Route =>
  (Object.entries(routePaths).find(([, path]) => path === location.hash.replace("#/", ""))?.[0] as Route) ?? "dashboard";

export function renderRoute(
  route: Route,
  user: PlatformUser,
  setUser: Dispatch<SetStateAction<PlatformUser | null>>,
) {
  const role = user.role as Role;
  if (route === "dashboard") return <Dashboard user={user} />;
  if (route === "tenants") return <TenantsPage />;
  if (route === "programs") return <ProgramsPage role={role} />;
  if (route === "households") return <Intake beneficiary={false} role={role} />;
  if (route === "beneficiaries") return <Intake beneficiary role={role} />;
  if (route === "eligibility") return <EligibilityPage role={role} />;
  if (route === "enrollment") return <EnrollmentModalityPage role={role} />;
  if (route === "payments") return <PaymentsPage role={role} />;
  if (route === "pdm") return <PdmPage user={user} />;
  if (route === "complaints") return <ComplaintsPage role={role} />;
  if (route === "activities") return <ActivitiesPage role={role} />;
  if (route === "imports") return <ImportsPage />;
  if (route === "automation") return <AutomationPage role={role} />;
  if (route === "assistance") return <AssistancePage role={role} />;
  if (route === "nfi_entitlements") return <EntitlementsPage role={role} />;
  if (route === "distribution_events") return <DistributionEventsPage role={role} />;
  if (route === "delivery_review") return <DeliveryReviewPage role={role} />;
  if (route === "medical") return <MedicalPage role={role} view="overview" />;
  if (route === "medical_inventory") return <MedicalPage role={role} view="inventory" />;
  if (route === "medical_entitlements") return <EntitlementsPage role={role} initialModality="MEDICAL" />;
  if (route === "medical_distribution") return <DistributionEventsPage role={role} initialModality="MEDICAL" />;
  if (route === "medical_equipment") return <MedicalPage role={role} view="equipment" />;
  if (route === "users") return <UsersPage system={user.is_superuser} />;
  if (route === "profile") return <ProfilePage user={user} onUpdated={setUser} />;
  return <AuditPage />;
}
