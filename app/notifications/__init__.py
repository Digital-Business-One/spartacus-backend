from app.notifications.adapters.mailersend import MailerSendAdapter
from app.notifications.dispatcher import NotificationDispatcher

dispatcher = NotificationDispatcher(port=MailerSendAdapter())
