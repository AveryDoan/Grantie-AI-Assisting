// The form's own wording for each field, so the review screen never shows internal field names.
export const FIELD_LABELS: Record<string, string> = {
  applicant_name: "Full name", date_of_birth: "Date of birth", email: "Email address", phone: "Phone number", nationality: "Nationality",
  passport_number: "Passport number", australian_or_nz_citizen_or_pr: "Australian or New Zealand citizen or permanent resident",
  residential_address: "Residential address", residential_country: "Country of residence", postal_address: "Postal address", postal_country: "Postal country",
  education_provider: "Education provider", course_name: "Course name", course_start_date: "Course start date", course_end_date: "Course end date",
  study_load: "Study load", arrival_date: "Date of arrival in the NT", under_18: "Under 18", declaration_agreed: "Declaration agreed",
  declaration_name: "Declaration name", declaration_date: "Declaration date",
  current_study: "What are you studying now, and where?", academic_achievements: "Academic achievements", leadership: "Leadership",
  community_engagement: "Community engagement", nt_contribution: "How will studying in the NT contribute?", biography: "Biography",
};
export const DOCUMENT_FIELD_LABELS: Record<string, string> = { provider_name: "Provider", full_name: "Name", course_name: "Course" };

export function fieldLabel(key: string): string {
  if (FIELD_LABELS[key]) return FIELD_LABELS[key];
  const s = key.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
