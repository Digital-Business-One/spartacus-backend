from app.notifications.adapters.firestore_mail import FirestoreMailAdapter
from app.notifications.dispatcher import NotificationDispatcher

dispatcher = NotificationDispatcher(port=FirestoreMailAdapter())
