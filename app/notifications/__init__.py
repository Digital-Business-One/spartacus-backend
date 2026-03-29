from app.notifications.adapters.firestore_mail import NotificationAdapter
from app.notifications.dispatcher import NotificationDispatcher

dispatcher = NotificationDispatcher(port=NotificationAdapter())
