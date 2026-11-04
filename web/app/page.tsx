import { Chat } from "./chat";

export const dynamic = "force-dynamic";

export default function HomePage() {
  const developmentMode = (process.env.APP_ENV ?? "development") === "development";

  return (
    <main className="stage">
      <Chat developmentMode={developmentMode} />
    </main>
  );
}
