import membershipNotice from '../../config/privacy_membership_notice.json';

export type PrivacyMembershipNotice = {
  version: string;
  effective_date: string;
  title: string;
  purpose: string;
  items: string[];
  retention: string;
  refusal: string;
  consent_label: string;
};

export const privacyMembershipNotice: PrivacyMembershipNotice = membershipNotice;
