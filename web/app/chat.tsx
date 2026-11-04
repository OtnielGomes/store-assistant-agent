"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

type Role = "customer" | "assistant";

type ChatMessage = {
  id: string;
  role: Role;
  text: string;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

function MessageBubble({ role, text }: { role: Role; text: string }) {
  const className = role === "customer" ? "message-row mine" : "message-row";
  return (
    <div className={className}>
      <p>{text}</p>
    </div>
  );
}

const typingIndicator = (
  <div className="typing">
    <span className="typing-label">Lia está digitando</span>
    <span />
    <span />
    <span />
  </div>
);

export function Chat() {
  const [conversationId, setConversationId] = useState(() => crypto.randomUUID());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [isWaiting, setIsWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const conversationRef = useRef(conversationId);

  useEffect(() => {
    const thread = threadRef.current;
    if (!thread) {
      return;
    }
    thread.scrollTop = thread.scrollHeight;
  }, [messages.length, isWaiting]);

  function startNewConversation() {
    const nextId = crypto.randomUUID();
    conversationRef.current = nextId;
    setConversationId(nextId);
    setMessages([]);
    setDraft("");
    setError(null);
    setIsWaiting(false);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || isWaiting) {
      return;
    }

    const activeConversation = conversationId;
    setDraft("");
    setError(null);
    setIsWaiting(true);
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "customer", text },
    ]);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: activeConversation,
          message: text,
        }),
      });
      if (!response.ok) {
        throw new Error("request failed");
      }
      const body = (await response.json()) as { reply: string };
      if (conversationRef.current !== activeConversation) {
        return;
      }
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "assistant", text: body.reply },
      ]);
    } catch {
      if (conversationRef.current === activeConversation) {
        setError("A Lia não conseguiu responder agora. Tente de novo.");
      }
    } finally {
      if (conversationRef.current === activeConversation) {
        setIsWaiting(false);
      }
    }
  }

  return (
    <section className="phone" aria-label="Conversa com a Lia">
      <header className="phone-header">
        <div>
          <h1 className="wordmark">Empório da Música</h1>
          <p className="presence">
            <span className="presence-dot" aria-hidden="true" />
            Lia, assistente virtual
          </p>
        </div>
        <button type="button" className="new-chat" onClick={startNewConversation}>
          Nova conversa
        </button>
      </header>
      <div className="thread" ref={threadRef} aria-live="polite">
        {messages.length === 0 ? (
          <p className="empty-thread">Escreva uma mensagem para a Lia.</p>
        ) : (
          messages.map((message) => (
            <MessageBubble key={message.id} role={message.role} text={message.text} />
          ))
        )}
        {isWaiting ? typingIndicator : null}
      </div>
      {error ? <p className="chat-error">{error}</p> : null}
      <form className="composer" onSubmit={handleSubmit}>
        <input
          aria-label="Mensagem para a Lia"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Escreva sua mensagem"
          autoComplete="off"
        />
        <button className="send" type="submit" disabled={isWaiting || draft.trim().length === 0}>
          Enviar
        </button>
      </form>
    </section>
  );
}
