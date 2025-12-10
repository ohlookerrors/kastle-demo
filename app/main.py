from fastapi import FastAPI
from app.routers.outbound_call import outbound_router

app = FastAPI()

app.include_router(outbound_router, prefix="/outbound")
