from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.sql import text
import redis

from app.config import settings
from app.database import engine, Base, get_db
from app.api.v1.jobs import router as jobs_router
from app.celery_app import celery_app

# Initialize database tables
Base.metadata.create_all(bind=engine)

# Create FastAPI instance
app = FastAPI(
    title="AI-Powered Transaction Processing Pipeline API",
    description="Asynchronously clean, parse, classify, and generate insights from transaction CSVs.",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the jobs router at the root level
app.include_router(jobs_router)


@app.get("/")
def read_root():
    return {
        "message": "Welcome to the AI-Powered Transaction Processing Pipeline API!",
        "docs_url": "/docs",
        "status": "online"
    }


@app.get("/health")
def health_check(db=Depends(get_db)):
    health_status = {
        "status": "healthy",
        "services": {
            "database": "unhealthy",
            "redis": "unhealthy"
        }
    }
    
    # Check Database
    try:
        db.execute(text("SELECT 1"))
        health_status["services"]["database"] = "healthy"
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["services"]["database"] = f"error: {str(e)}"
        
    # Check Redis
    try:
        r = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=1)
        r.ping()
        health_status["services"]["redis"] = "healthy"
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["services"]["redis"] = f"error: {str(e)}"
        
    return health_status

