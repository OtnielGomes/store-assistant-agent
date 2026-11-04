import type { Metadata } from "next";
import { Fraunces, Literata } from "next/font/google";

import "./globals.css";

const display = Fraunces({
  subsets: ["latin"],
  variable: "--font-display-family",
});

const body = Literata({
  subsets: ["latin"],
  variable: "--font-body-family",
});

export const metadata: Metadata = {
  title: "Lia · Empório da Música",
  description: "Conversa com a assistente virtual do Empório da Música.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR" className={`${display.variable} ${body.variable}`}>
      <body>{children}</body>
    </html>
  );
}
