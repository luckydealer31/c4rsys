import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Client, JournalEntry, Order, Rental } from "../../api/types";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { clientName } from "../clients/ClientsPage";
import { PaymentButton } from "../finance/CashReceipt";
import { JournalTable } from "../settings/EmployeesPage";
import { BookingCard, type CardAction } from "./Booking";
import { DepositCard } from "./Deposit";
import { CalculationView } from "./IssuePage";
import { TransferRental } from "./RentalOrder";
import { RentalServices } from "./Services";

/** The card of a booking (decision of 16.11.2026): over the whole screen; one block «Booking»
 * — vehicle, tariff, client, place of the issue, planned period, place of the return, order,
 * comment, auto-cancel — with its actions as buttons, the transfer to another order and the
 * history among them; the services, the deposit and the calculation with its total beside.
 * No actual start and no inspections: those belong to a rental. */
export function BookingView({
  rental,
  vehicleName,
  client,
  locationName,
  history,
  onDone,
}: {
  rental: Rental;
  vehicleName: string;
  client: Client | null;
  locationName: (id: string | null) => string;
  history: { entries: JournalEntry[]; loading: boolean; error: ApiError | null };
  onDone: (next?: Rental) => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const booking = rental.booking;
  if (!booking) return null;
  const booked = rental.state === "booked";
  const more: CardAction[] = [];
  if (booked && can("rental.transfer")) {
    more.push({
      key: "transfer",
      label: t("bookings.action_transfer"),
      body: <TransferRental rental={rental} onDone={onDone} />,
    });
  }
  more.push({
    key: "history",
    label: t("rentals.history"),
    body: (
      <div>
        {history.loading ? <Loading /> : null}
        <ErrorMessage error={history.error} />
        <JournalTable entries={history.entries} timeZone={rental.calc_timezone} />
      </div>
    ),
  });
  return (
    <div className="page full">
      <h1>
        {vehicleName} · {t("bookings.title", { number: booking.number })}
        {rental.state === "cancelled" ? ` · ${t("rentals.state_cancelled")}` : ""}
      </h1>
      <div className="cols-2">
        <Card title={t("bookings.block")}>
          <BookingCard
            rental={rental}
            onDone={onDone}
            merged
            more={more}
            lead={
              <>
                <dt>{t("rentals.vehicle")}</dt>
                <dd>{vehicleName}</dd>
                <dt>{t("rentals.tariff")}</dt>
                <dd>{rental.tariff_snapshot ? `${rental.tariff_snapshot.name} · ${rental.tariff_snapshot.currency}` : "—"}</dd>
                <dt>{t("rentals.client")}</dt>
                <dd>
                  {client ? <Link to={companyPath(company, `/clients/${client.id}`)}>{clientName(client)}</Link> : "—"}
                </dd>
                <dt>{t("bookings.issue_place")}</dt>
                <dd>{locationName(rental.issue_location_id)}</dd>
              </>
            }
            middle={
              <>
                <dt>{t("bookings.return_place")}</dt>
                <dd>{locationName(rental.planned_return_location_id)}</dd>
                <dt>{t("rentals.order")}</dt>
                <dd>
                  <Link to={companyPath(company, `/orders/${rental.order_id}`)}>
                    {t("bookings.order_number", { number: rental.order_number })}
                  </Link>
                </dd>
              </>
            }
          />
        </Card>
        <div>
          {booked || rental.services?.length ? (
            <Card title={t("services.title")}>
              {rental.services_not_allowed?.length ? <p className="alert warn">{t("changes.services_not_allowed")}</p> : null}
              <RentalServices rental={rental} onDone={onDone} />
            </Card>
          ) : null}
          <Card title={t("deposit.title")}>
            <DepositCard rental={rental} onDone={() => onDone()} />
          </Card>
          <Card title={t("rentals.calculation")}>
            <Total rental={rental} />
            {booked ? (
              <PaymentButton
                orderId={rental.order_id}
                clientId={rental.client_id}
                prefer={rental.calculation?.currency}
                onDone={() => onDone()}
              />
            ) : null}
          </Card>
        </div>
      </div>
    </div>
  );
}

/** The calculation with its total: the rental by the tariff, the linked services, their sum,
 * and what is already paid to the order. The deposit is not here (decision of 16.11.2026): it
 * has a block and a button of its own. */
function Total({ rental }: { rental: Rental }) {
  const { t } = useTranslation();
  const company = useCompany();
  const order = useApi<{ order: Order }>(
    ["order", company.public_id, rental.order_id],
    `/c/${company.public_id}/orders/${rental.order_id}`,
  );
  const calculation = rental.calculation;
  if (!calculation) return <p className="muted">{t("bookings.no_calculation")}</p>;
  const currency = calculation.currency;
  const services = (rental.services ?? [])
    .filter((line) => line.rental_id === rental.id && line.state !== "cancelled" && line.calculation?.currency === currency)
    .reduce((sum, line) => sum + Number(line.calculation?.amount ?? 0), 0);
  const base = Number(calculation.base_amount);
  const paid = order.data?.order.amounts[currency]?.paid;
  return (
    <dl className="total-lines">
      <dt>{t("bookings.total_rental")}</dt>
      <dd>
        <CalculationView calculation={calculation} inline />
      </dd>
      <dt>{t("services.title")}</dt>
      <dd>
        {services.toFixed(2)} {currency}
      </dd>
      <dt className="sum">{t("bookings.total")}</dt>
      <dd className="sum">
        {(base + services).toFixed(2)} {currency}
      </dd>
      {paid !== undefined ? (
        <>
          <dt>{t("bookings.paid")}</dt>
          <dd>
            {paid} {currency}
          </dd>
        </>
      ) : null}
    </dl>
  );
}
