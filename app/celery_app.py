from celery import Celery
from app.config import settings

# Initialize Celery app
celery_app = Celery(
    "alemeno_pipeline",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)

# Configuration overrides if needed
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    imports=["app.tasks.pipeline"]
)

# Autodiscover tasks in the app package
celery_app.autodiscover_tasks(["app"])
