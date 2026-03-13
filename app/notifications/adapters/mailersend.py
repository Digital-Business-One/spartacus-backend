from mailersend import EmailBuilder, MailerSendClient

from app.logging.decorator import log


class MailerSendAdapter:
    _FROM_EMAIL = "noreply@horadofluxo.com.br"
    _FROM_NAME = "Spartacus"

    @log(mask=["to"])
    def send(
        self, event_id: str, template_id: str, to: str, data: dict
    ) -> None:
        email_request = (
            EmailBuilder()
            .from_email(self._FROM_EMAIL, self._FROM_NAME)
            .to(to)
            .template_id(template_id)
            .personalization([{"email": to, "data": data}])
            .build()
        )
        MailerSendClient().emails.send(email_request)
