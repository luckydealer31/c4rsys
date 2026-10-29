import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { Delivery, DeliveryService } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useArticleName, useArticles } from "../finance/Articles";
import { useCashAccounts } from "../finance/FinancePage";

type Result = { delivery: Delivery };

export function useDeliveryServices(enabled = true) {
  const company = useCompany();
  return useApi<{ services: DeliveryService[] }>(
    ["delivery-services", company.public_id],
    `/c/${company.public_id}/delivery-services`,
    enabled,
  );
}

function refreshKeys() {
  return ["deliveries", "delivery", "orders", "order", "delivery-services", "cash-accounts"];
}

/** A service of the catalog or a price by hand; changing the substituted price needs its right
 * (§ 15.2.6; decision Д-5). */
export function PriceFields({
  services,
  serviceId,
  setServiceId,
  price,
  setPrice,
}: {
  services: DeliveryService[];
  serviceId: string;
  setServiceId: (v: string) => void;
  price: string;
  setPrice: (v: string) => void;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const chosen = services.find((x) => x.id === serviceId);
  return (
    <div className="row">
      <Field label={t("trips.service")}>
        <select value={serviceId} onChange={(e) => { setServiceId(e.target.value); setPrice(""); }}>
          <option value="">{t("trips.price_by_hand")}</option>
          {services
            .filter((x) => x.state === "active")
            .map((x) => (
              <option key={x.id} value={x.id}>
                {x.name} · {Number(x.price)} {x.currency}
              </option>
            ))}
        </select>
      </Field>
      <Field label={`${t("trips.price")}, ${chosen?.currency ?? company.main_currency ?? ""}`} hint={chosen ? t("trips.price_override_hint") : undefined}>
        <input inputMode="decimal" value={price} placeholder={chosen ? String(Number(chosen.price)) : ""} onChange={(e) => setPrice(e.target.value)} />
      </Field>
    </div>
  );
}

/** The paid position of a client delivery, kept apart from the operation (§ 15.2.2). */
export function TripPrice({ d }: { d: Delivery }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const services = useDeliveryServices(can("delivery.price"));
  const [serviceId, setServiceId] = useState(d.service_id ?? "");
  const [price, setPrice] = useState(d.price ? String(Number(d.price)) : "");
  const change = useCommand<Record<string, unknown>, Result>(`/c/${company.public_id}/deliveries/${d.id}/price`);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await change.run({
      expected_version: d.version,
      service_id: serviceId || null,
      price: price.trim().replace(",", ".") || null,
    });
    if (result) for (const key of refreshKeys()) await queryClient.invalidateQueries({ queryKey: [key] });
  }
  return (
    <Card title={t("trips.price_title")}>
      <p>{d.price !== null && d.price !== undefined ? `${Number(d.price)} ${d.currency ?? ""}` : t("trips.no_price")}</p>
      {can("delivery.price") && d.state !== "cancelled" ? (
        <form className="stack" onSubmit={submit}>
          <PriceFields
            services={services.data?.services ?? []}
            serviceId={serviceId}
            setServiceId={setServiceId}
            price={price}
            setPrice={setPrice}
          />
          <ErrorMessage error={change.error} />
          <div className="actions">
            <button type="submit" className="secondary" disabled={change.pending || (!serviceId && !price.trim())}>
              {t("common.save")}
            </button>
          </div>
        </form>
      ) : null}
    </Card>
  );
}

/** Actual expenses of the company on the trip: payouts with an expense article (§ 15.2.3;
 * decision Д-6). */
export function TripExpenses({ d }: { d: Delivery }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const accounts = useCashAccounts();
  const articles = useArticles();
  const articleName = useArticleName();
  const [accountId, setAccountId] = useState("");
  const [articleId, setArticleId] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const add = useCommand<Record<string, unknown>, Result>(`/c/${company.public_id}/deliveries/${d.id}/expenses`);
  const account = (accounts.data?.cash_accounts ?? []).find((a) => a.id === accountId);
  const names = new Map((articles.data?.articles ?? []).map((a) => [a.id, articleName(a)]));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await add.run({
      cash_account_id: accountId,
      article_id: articleId,
      amount: amount.trim().replace(",", "."),
      currency: account?.currency,
      note,
    });
    if (result) {
      setAmount("");
      setNote("");
      for (const key of refreshKeys()) await queryClient.invalidateQueries({ queryKey: [key] });
    }
  }
  const items = d.expenses ?? [];
  return (
    <Card title={t("trips.expenses")}>
      {items.length === 0 ? <p className="muted">{t("trips.no_expenses")}</p> : null}
      {items.map((e) => (
        <div key={e.id} className="small">
          № {e.number} · {Number(e.amount)} {e.currency} · {e.article_id ? (names.get(e.article_id) ?? "") : ""}
          {e.note ? ` · ${e.note}` : ""}
        </div>
      ))}
      {can("finance.payout") && d.state !== "cancelled" ? (
        <form className="row" onSubmit={submit} style={{ marginTop: 8 }}>
          <Field label={t("trips.account")}>
            <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              <option value="">—</option>
              {(accounts.data?.cash_accounts ?? [])
                .filter((a) => a.state === "active")
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {a.currency}
                  </option>
                ))}
            </select>
          </Field>
          <Field label={t("trips.article")}>
            <select value={articleId} onChange={(e) => setArticleId(e.target.value)}>
              <option value="">—</option>
              {(articles.data?.articles ?? [])
                .filter((a) => a.kind === "expense" && a.state === "active")
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {articleName(a)}
                  </option>
                ))}
            </select>
          </Field>
          <Field label={`${t("trips.amount")}${account ? `, ${account.currency}` : ""}`}>
            <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label={t("trips.comment")}>
            <input value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="submit" className="secondary" disabled={add.pending || !accountId || !articleId || !amount.trim()}>
              {t("trips.add_expense")}
            </button>
          </div>
          <ErrorMessage error={add.error} />
        </form>
      ) : null}
    </Card>
  );
}

/** The catalog of delivery services: a name and a base price (§ 15.2.6). */
export function DeliveryServicesPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const services = useDeliveryServices();
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");
  const create = useCommand<Record<string, unknown>, { service: DeliveryService }>(`/c/${company.public_id}/delivery-services`);
  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["delivery-services"] });
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await create.run({ name, price: price.trim().replace(",", ".") });
    if (result) {
      setName("");
      setPrice("");
      await refresh();
    }
  }
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/trips")}>← {t("nav.trips")}</Link>
      </p>
      <h1>{t("trips.catalog")}</h1>
      <Card>
        {services.isLoading ? <Loading /> : null}
        {services.data?.services.length === 0 ? <p className="muted">{t("trips.no_services")}</p> : null}
        {(services.data?.services ?? []).map((x) => (
          <ServiceRow key={x.id} service={x} onChange={refresh} />
        ))}
      </Card>
      {can("delivery.catalog") ? (
        <Card title={t("trips.new_service")}>
          <form className="row" onSubmit={submit}>
            <Field label={t("tariffs.name")}>
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label={`${t("trips.price")}, ${company.main_currency ?? ""}`}>
              <input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
            </Field>
            <div className="actions">
              <button type="submit" disabled={create.pending || !name.trim() || !price.trim()}>
                {t("trips.add_service")}
              </button>
            </div>
            <ErrorMessage error={create.error} />
          </form>
        </Card>
      ) : null}
    </div>
  );
}

function ServiceRow({ service, onChange }: { service: DeliveryService; onChange: () => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const change = useCommand<Record<string, unknown>, { service: DeliveryService }>(
    `/c/${company.public_id}/delivery-services/${service.id}`,
    "PATCH",
  );
  const archived = service.state === "archived";
  return (
    <div className="list-link">
      <strong>{service.name}</strong> · {Number(service.price)} {service.currency}{" "}
      {archived ? <span className="badge">{t("tariffs.archived")}</span> : null}
      {can("delivery.catalog") ? (
        <button
          type="button"
          className="secondary small"
          disabled={change.pending}
          onClick={() => void change.run({ archived: !archived, expected_version: service.version }).then((r) => (r ? onChange() : undefined))}
        >
          {archived ? t("service.restore") : t("service.archive")}
        </button>
      ) : null}
      <ErrorMessage error={change.error} />
    </div>
  );
}
