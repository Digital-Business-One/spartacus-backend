from mailersend import EmailBuilder, MailerSendClient

from app.logging.decorator import log

_STYLE = """
  body { margin:0; padding:0; background:#0B0D12; font-family:sans-serif;
         color:#E8E8E8; }
  .wrap { max-width:600px; margin:0 auto; padding:32px 24px; }
  .logo { font-size:22px; font-weight:700; color:#C6A34E; letter-spacing:2px; }
  h1 { font-size:20px; color:#C6A34E; margin:24px 0 8px; }
  p { font-size:15px; line-height:1.6; margin:8px 0; color:#C8C8C8; }
  .btn { display:inline-block; margin:24px 0; padding:14px 32px;
         background:#C6A34E; color:#0B0D12; font-weight:700; font-size:15px;
         border-radius:6px; text-decoration:none; }
  table { width:100%; border-collapse:collapse; margin:16px 0; }
  th { text-align:left; padding:8px 12px; background:#1A1D26; color:#C6A34E;
       font-size:13px; border-bottom:1px solid #2A2D36; }
  td { padding:8px 12px; font-size:13px; border-bottom:1px solid #1A1D26;
       color:#C8C8C8; }
  .footer { margin-top:40px; font-size:12px; color:#555; }
"""

_ROLES_PT = {
    "student": "Aluno",
    "teacher": "Professor",
    "instructor": "Instrutor",
    "guardian": "Responsável",
    "supporter": "Apoiador",
    "sponsor": "Patrocinador",
}


def _roles_label(roles: list[str]) -> str:
    return ", ".join(_ROLES_PT.get(r, r) for r in roles)


class EmailService:
    _FROM_EMAIL = "noreply@horadofluxo.com.br"
    _FROM_NAME = "Spartacus"

    @log(mask=["to"])
    def send(self, to: str, subject: str, html: str) -> None:
        email_request = (
            EmailBuilder()
            .from_email(self._FROM_EMAIL, self._FROM_NAME)
            .to(to)
            .subject(subject)
            .html(html)
            .build()
        )
        MailerSendClient().emails.send(email_request)

    def send_signup_confirmation(
        self,
        to: str,
        name: str,
        link: str,
        roles: list[str],
        class_ids: list[str],
        dependents: list,
    ) -> None:
        show_classes = bool(class_ids) and any(
            r in roles for r in ("student", "teacher", "instructor")
        )
        show_dependents = "guardian" in roles and dependents

        classes_section = ""
        if show_classes:
            rows = "".join(f"<tr><td>{cid}</td></tr>" for cid in class_ids)
            classes_section = (
                "<h1>Turmas inscritas</h1>"
                "<table><thead><tr><th>ID da turma</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
            )

        dependents_section = ""
        if show_dependents:
            rows = "".join(
                f"<tr><td>{d.name}</td><td>{d.birth_date}</td></tr>"
                for d in dependents
            )
            dependents_section = (
                "<h1>Dependentes cadastrados</h1>"
                "<table><thead>"
                "<tr><th>Nome</th><th>Data de nascimento</th></tr>"
                f"</thead><tbody>{rows}</tbody></table>"
            )

        roles_str = _roles_label(roles)
        html = (
            f"<!DOCTYPE html><html><head><style>{_STYLE}</style></head>"
            "<body><div class=\"wrap\">"
            "<div class=\"logo\">SPARTACUS</div>"
            "<h1>Confirme seu e-mail</h1>"
            f"<p>Olá, <strong>{name}</strong>!</p>"
            f"<p>Seu cadastro foi recebido como <strong>{roles_str}</strong>."
            " Para prosseguir, confirme seu e-mail clicando no botão abaixo.</p>"
            f"<a class=\"btn\" href=\"{link}\">Confirmar e-mail</a>"
            "<p>O link expira em 24 horas. Após a confirmação, sua conta"
            " passará pela aprovação do responsável do projeto.</p>"
            f"{classes_section}{dependents_section}"
            "<div class=\"footer\">"
            "Projeto Spartacus Artes Marciais — Brasnorte, MT"
            "</div></div></body></html>"
        )

        self.send(to, "Confirme seu e-mail — Spartacus", html)

    def send_verification_link(self, to: str, name: str, link: str) -> None:
        html = (
            f"<!DOCTYPE html><html><head><style>{_STYLE}</style></head>"
            "<body><div class=\"wrap\">"
            "<div class=\"logo\">SPARTACUS</div>"
            "<h1>Novo link de confirmação</h1>"
            f"<p>Olá, <strong>{name}</strong>!</p>"
            "<p>Você solicitou um novo link de confirmação."
            " Clique no botão abaixo:</p>"
            f"<a class=\"btn\" href=\"{link}\">Confirmar e-mail</a>"
            "<p>O link expira em 24 horas.</p>"
            "<div class=\"footer\">"
            "Projeto Spartacus Artes Marciais — Brasnorte, MT"
            "</div></div></body></html>"
        )

        self.send(to, "Novo link de confirmação — Spartacus", html)
