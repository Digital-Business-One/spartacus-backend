from mailersend import EmailBuilder, MailerSendClient

from app.logging.decorator import log


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
