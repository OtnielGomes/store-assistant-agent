"""Portuguese correctness-and-tone judge. The callable is injectable."""

from lia_api.settings import Settings

_INSTRUCTIONS = """\
Você avalia a resposta da Lia, assistente virtual do Empório da Música.
Julgue só duas coisas, em português do Brasil:
- correção: a resposta cobre os fatos esperados, sem inventar preço,
  prazo, produto ou política;
- tom: atendimento de loja, educado, em português,
  como assistente virtual quando couber.
Uma resposta em outro idioma, grosseira, ou que invente um fato, não passa.
Não exija telefone nem e-mail na resposta.
Responda apenas o objeto pedido.
"""


def openai_judge(settings: Settings):
    """Build a judge that calls the configured chat model. Tests do not use this."""
    from pydantic import BaseModel

    from lia_api.model import build_chat_model

    class Grade(BaseModel):
        correct: bool
        portuguese_store_tone: bool
        reasoning: str

    structured = build_chat_model(settings).with_structured_output(Grade)

    def judge(reply: str, expected: dict[str, object], messages: list[str]) -> bool:
        grade = structured.invoke(
            [
                {"role": "system", "content": _INSTRUCTIONS},
                {
                    "role": "user",
                    "content": (
                        f"Mensagens do cliente: {messages}\n"
                        f"Fatos esperados: {expected}\n"
                        f"Resposta da Lia: {reply}"
                    ),
                },
            ]
        )
        correct = grade.correct if isinstance(grade, Grade) else bool(grade["correct"])
        tone = (
            grade.portuguese_store_tone
            if isinstance(grade, Grade)
            else bool(grade["portuguese_store_tone"])
        )
        return correct and tone

    return judge
