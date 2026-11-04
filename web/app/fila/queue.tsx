"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type HandoffRow = {
  id: number;
  reason: string;
  summary: string;
  order_id: number | null;
  created_at: string;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

const REASON_LABELS: Record<string, string> = {
  customer_request: "Pedido de atendimento humano",
  exchange_return_refund_warranty: "Troca, devolução, reembolso ou garantia",
  complaint: "Reclamação",
  value_question: "Dúvida sobre valor",
  overdue_delivery: "Entrega atrasada",
  coming_soon_item: "Item em breve",
  data_deletion_request: "Pedido de exclusão de dados",
  policy_gap: "Assunto fora do manual",
  verification_locked: "Verificação bloqueada",
  output_check_failure: "Falha na conferência da resposta",
};

const timeFormat = new Intl.DateTimeFormat("pt-BR", {
  timeZone: "America/Campo_Grande",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function Queue() {
  const [rows, setRows] = useState<HandoffRow[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    fetch(`${API_URL}/handoffs`)
      .then((response) => {
        if (!response.ok) {
          throw new Error("request failed");
        }
        return response.json() as Promise<HandoffRow[]>;
      })
      .then((body) => {
        if (active) {
          setRows(body);
        }
      })
      .catch(() => {
        if (active) {
          setFailed(true);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section className="ledger" aria-label="Fila de atendimento">
      <header className="phone-header">
        <div>
          <h1 className="wordmark">Fila de atendimento</h1>
          <p className="presence">Encaminhamentos para a equipe</p>
        </div>
        <Link className="sheet-link" href="/">
          Voltar à conversa
        </Link>
      </header>
      <div className="ledger-body">
        {failed ? (
          <p className="empty-thread">A fila não carregou. Atualize a página.</p>
        ) : rows === null ? (
          <p className="empty-thread">Carregando a fila.</p>
        ) : rows.length === 0 ? (
          <p className="empty-thread">Nenhum atendimento na fila.</p>
        ) : (
          rows.map((row) => (
            <article key={row.id} className="queue-entry">
              <h2 className="queue-reason">{REASON_LABELS[row.reason] ?? row.reason}</h2>
              <p className="queue-summary">{row.summary}</p>
              <p className="queue-meta">
                {row.order_id !== null ? `Pedido ${row.order_id}` : "Sem número de pedido"}
                <br />
                <time dateTime={row.created_at}>
                  {timeFormat.format(new Date(row.created_at))}
                </time>
              </p>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
