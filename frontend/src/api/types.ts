export type User = {
  public_id: string;
  first_name: string;
  last_name: string;
  middle_name: string;
  has_full_name: boolean;
  email: string | null;
  language: string;
  theme: "system" | "light" | "dark";
};

export type CompanySummary = {
  public_id: string;
  display_name: string;
  setup: { profile: boolean; finance: boolean };
};

export type Company = CompanySummary & {
  timezone: string | null;
  main_currency: string | null;
  created_at: string;
  version: number;
  employment?: {
    id: string;
    position_code: string;
    position_name: string;
    is_top_authority: boolean;
  };
};

export type StartedFlow = { flow: string; resend_after_seconds: number; code_ttl_seconds: number };

export type Session = {
  id: string;
  created_at: string;
  last_activity_at: string;
  user_agent: string;
  ip: string | null;
  current: boolean;
};
