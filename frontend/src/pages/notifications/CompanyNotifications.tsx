import { useCompany } from "../../layout/CompanyShell";
import { NotificationsPage } from "./NotificationsPage";

/** The centre opened by the bell inside a company (decision of 16.11.2026): the menu of the
 * company stays, the notifications of that company come first. A file of its own: the bell
 * lives in the frame that the frame of a company is built on. */
export function CompanyNotificationsPage() {
  const company = useCompany();
  return <NotificationsPage key={company.public_id} current={company.public_id} />;
}
