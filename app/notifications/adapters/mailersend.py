from mailersend import EmailBuilder, MailerSendClient

from app.logging.decorator import log


class MailerSendAdapter:
    _FROM_EMAIL = "noreply@spartacus.app.br"
    _FROM_NAME = "Spartacus Artes Marciais"

    @log(mask=["to"])
    def send(
        self, event_id: str, template_id: str, to: str, data: dict
    ) -> None:
        email_request = (
            EmailBuilder()
            .from_email(self._FROM_EMAIL, self._FROM_NAME)
            .to(to)
            .subject("")
            .template(template_id)
            .personalize_many([{"email": to, "data": data}])
            .build()
        )
        MailerSendClient().emails.send(email_request)
