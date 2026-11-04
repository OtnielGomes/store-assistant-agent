import type { Metadata } from "next";

import { Queue } from "./queue";

export const metadata: Metadata = {
  title: "Fila de atendimento",
  description: "Fila de encaminhamentos para a equipe do Empório da Música.",
};

export default function QueuePage() {
  return (
    <main className="stage">
      <Queue />
    </main>
  );
}
