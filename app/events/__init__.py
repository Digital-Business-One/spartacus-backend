from app.events.adapters.firestore import FirestoreEventStore
from app.events.publisher import EventPublisher

publisher = EventPublisher(port=FirestoreEventStore())
