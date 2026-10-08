import type { Entity } from "./types/types";

export const valueOf = (record: Entity | undefined, key: string) => {
  if (!record) return "";
  const value = String(record[key] ?? "");
  return key === "relationship_type" && value === "FS"
    ? "Finish to start"
    : value;
};
export const programLabel = (program: Entity) => program.name || "Unnamed program";
export const householdLabel = (household: Entity) =>
  `${valueOf(household, "registration_reference") || "Household"} — ${valueOf(household, "program_name") || "Program"} — ${valueOf(household, "location") || "Location not recorded"} — size ${valueOf(household, "household_size") || "?"}`;
export const beneficiaryLabel = (beneficiary: Entity) =>
  `${valueOf(beneficiary, "national_id_reference") || "National ID"} — ${beneficiary.full_name || "Unnamed"}`;
export const enrollmentLabel = (enrollment: Entity) =>
  `${valueOf(enrollment, "beneficiary_number") || "Beneficiary"} — ${valueOf(enrollment, "beneficiary_name") || "Unnamed"} (${valueOf(enrollment, "program_name") || "Program"})`;
export const nfiBeneficiaryLabel = (record: Entity) => {
  const nationalIdReference = valueOf(record, "national_id_reference");
  return [
    valueOf(record, "beneficiary_name"),
    nationalIdReference === "Not recorded" ? "" : nationalIdReference,
  ]
    .filter(Boolean)
    .join(" - ");
};
export const nfiEntitlementLabel = (record: Entity) =>
  `${valueOf(record, "household_reference") || nfiBeneficiaryLabel(record) || "Household"} - ${valueOf(record, "item_name") || "NFI item"}`;
export const distributionEventLabel = (record: Entity) =>
  [valueOf(record, "event_date"), valueOf(record, "event_location") || valueOf(record, "location"), valueOf(record, "warehouse_name")]
    .filter(Boolean)
    .join(" - ") || "Distribution event";
export const readableCode = (value: unknown) =>
  String(value ?? "")
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (character) => character.toUpperCase());
