"""Direct task publication for the demo; durable delivery is a future improvement."""
from functools import lru_cache
from celery import Celery
from research_core.config import settings


@lru_cache
def publisher():
    app = Celery("backend", broker=settings().broker_url)
    app.conf.update(broker_connection_timeout=3, task_publish_retry=False,
                    broker_transport_options={"socket_connect_timeout": 3, "socket_timeout": 3})
    return app


def publish_ingestion(job_id):
    publisher().send_task("research.ingest", args=[job_id], task_id=job_id, retry=False)
