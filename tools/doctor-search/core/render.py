"""Plain-text replies in English and Brazilian Portuguese."""

from __future__ import annotations

TEXT = {
    "en": {
        "intake": "Please confirm request {id}:",
        "patient": "Patient", "zip": "Near ZIP", "miles": "Distance", "availability": "Availability",
        "visit_type": "Type", "specialty_text": "Looking for", "context": "Details", "cc": "CC",
        "plan_name": "Insurance plan", "language_pref": "Provider's language",
        "missing": "Still needed: {fields}",
        "shortlist": "Options for {id}, best match first:",
        "none": "No matching providers yet.",
        "summary": "Request {id}: {specialty} for {patient}, near {zip}.",
        "chosen": "Chosen:",
        "next": "Next step: I can contact these practices to ask for appointments.",
        "your_plan": "your plan",
        "in_network": "Takes {plan} (per their site)",
        "out_of_network": "Does not take {plan} (per their site)",
        "unknown": "Insurance not stated on their site",
        "reviews": "★ {avg} from {count} reviews ({sources})",
        "no_reviews": "No reviews found",
        "not_new": "Not taking new patients",
        "telehealth": "online visits",
        "routes": "Contact: {routes}",
        "booking": "online booking", "email": "email", "form": "contact form", "phone": "phone {phone}",
        "ready": "✅ {id}: I found {count} options for {specialty}.",
        "empty": "{id}: I didn't find practices that clearly offer {specialty} within {miles} mi. Ask me to search a wider area.",
        "failed": "⚠️ {id}: the search didn't finish ({reason}). Ask me to try again.",
    },
    "pt": {
        "intake": "Confirme o pedido {id}:",
        "patient": "Paciente", "zip": "Perto do ZIP", "miles": "Distância", "availability": "Disponibilidade",
        "visit_type": "Tipo", "specialty_text": "Procurando", "context": "Detalhes", "cc": "CC",
        "plan_name": "Plano de saúde", "language_pref": "Idioma do profissional",
        "missing": "Ainda falta: {fields}",
        "shortlist": "Opções para {id}, melhores primeiro:",
        "none": "Nenhum profissional encontrado ainda.",
        "summary": "Pedido {id}: {specialty} para {patient}, perto de {zip}.",
        "chosen": "Escolhidos:",
        "next": "Próximo passo: posso entrar em contato com esses consultórios pedindo horários.",
        "your_plan": "seu plano",
        "in_network": "Aceita {plan} (segundo o site)",
        "out_of_network": "Não aceita {plan} (segundo o site)",
        "unknown": "Plano não informado no site",
        "reviews": "★ {avg} em {count} avaliações ({sources})",
        "no_reviews": "Sem avaliações encontradas",
        "not_new": "Não está aceitando novos pacientes",
        "telehealth": "atende online",
        "routes": "Contato: {routes}",
        "booking": "agendamento online", "email": "email", "form": "formulário", "phone": "telefone {phone}",
        "ready": "✅ {id}: encontrei {count} opções para {specialty}.",
        "empty": "{id}: não encontrei consultórios que ofereçam claramente {specialty} em até {miles} mi. Peça para eu buscar numa área maior.",
        "failed": "⚠️ {id}: a busca não terminou ({reason}). Peça para eu tentar de novo.",
    },
}
VISIT = {"en": {"visit": "visit", "urgent": "urgent care", "lab": "lab test"},
         "pt": {"visit": "consulta", "urgent": "urgência", "lab": "exame"}}

DRAFT = {
    "en": ("Draft email for {id}\nTo: {to}\nCc: {cc}\nSubject: {subject}\n\n{body}\n\n"
           "If it looks right, send /ok {code} to email it.\nTo change something, just tell me."),
    "pt": ("Rascunho do email do pedido {id}\nPara: {to}\nCc: {cc}\nAssunto: {subject}\n\n{body}\n\n"
           "Se estiver certo, envie /ok {code} para mandar o email.\nPara mudar algo, é só me dizer."),
}


def _t(lang: str) -> dict:
    return TEXT.get(lang, TEXT["en"])


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def draft_notice(draft: dict, request_id: str, lang: str) -> str:
    return DRAFT.get(lang, DRAFT["en"]).format(
        id=request_id, to=draft["to"], cc=", ".join(draft["cc"]), subject=draft["subject"],
        body=draft["body"], code=draft["approval_code"])


def intake(req: dict, lang: str) -> str:
    t = _t(lang)
    lines = [t["intake"].format(id=req["id"])]
    for field in ("patient", "specialty_text", "visit_type", "zip", "miles", "availability",
                  "plan_name", "language_pref", "context", "cc"):
        value = req["intake"].get(field)
        if value:
            if field == "visit_type":
                value = VISIT[lang].get(value, value)
            if field == "miles":
                value = f"{float(value):g} mi"
            lines.append(f"- {t[field]}: {value}")
    if req["missing"]:
        lines.append(t["missing"].format(fields=", ".join(t[f] for f in req["missing"])))
    return "\n".join(lines)


def provider(c: dict, lang: str, plan: str | None) -> str:
    t = _t(lang)
    head = f"{c['rank']}. {c['name']}"
    if c.get("miles") is not None:
        head += f" — {c['miles']:g} mi"
    lines = [head]
    if c.get("match_evidence"):
        lines.append(f"   ✔ \"{_short(c['match_evidence'], 140)}\"")
    facts = [t[c.get("insurance", "unknown")].format(plan=plan or t["your_plan"])]
    if c.get("new_patients") == "no":
        facts.append(t["not_new"])
    if c.get("telehealth") == "yes":
        facts.append(t["telehealth"])
    lines.append("   " + " · ".join(facts))
    rating = c.get("rating")
    lines.append("   " + (t["reviews"].format(avg=f"{rating['avg']:.1f}", count=rating["count"],
                                              sources=", ".join(rating["sources"][:3]))
                          if rating else t["no_reviews"]))
    routes = [t["booking"]] if c.get("booking_url") else []
    if c.get("emails"):
        routes.append(t["email"])
    if c.get("contact_form_url"):
        routes.append(t["form"])
    if c.get("phone"):
        routes.append(t["phone"].format(phone=c["phone"]))
    if routes:
        lines.append("   " + t["routes"].format(routes=", ".join(routes)))
    if c.get("url"):
        lines.append("   " + c["url"][0])
    return "\n".join(lines)


def _providers(req: dict, lang: str, limit: int) -> list[str]:
    plan = req["intake"].get("plan_name")
    return [provider(c, lang, plan) for c in req["candidates"] if c.get("verified")][:limit]


def shortlist(req: dict, lang: str, limit: int = 5) -> str:
    items = _providers(req, lang, limit)
    if not items:
        return _t(lang)["none"]
    return "\n\n".join([_t(lang)["shortlist"].format(id=req["id"]), *items])


def shortlist_ready(req: dict, lang: str, limit: int = 5) -> str:
    items = _providers(req, lang, limit)
    head = _t(lang)["ready"].format(id=req["id"], count=len(req["candidates"]),
                                    specialty=req["intake"].get("specialty_text"))
    return "\n\n".join([head, *items])


def job_empty(req: dict, lang: str) -> str:
    return _t(lang)["empty"].format(id=req["id"], specialty=req["intake"].get("specialty_text"),
                                    miles=f"{float(req['intake'].get('max_miles', 0)):g}")


def job_failed(request_id: str, reason: str, lang: str) -> str:
    return _t(lang)["failed"].format(id=request_id, reason=reason)


def summary(req: dict, lang: str) -> str:
    t = _t(lang)
    i = req["intake"]
    by_rank = {c["rank"]: c for c in req["candidates"]}
    chosen = [by_rank[r] for r in req["choice"] if r in by_rank]
    return "\n\n".join([
        t["summary"].format(id=req["id"], specialty=i.get("specialty_text"),
                            patient=i.get("patient"), zip=i.get("zip")),
        t["chosen"], *(provider(c, lang, i.get("plan_name")) for c in chosen), t["next"]])
