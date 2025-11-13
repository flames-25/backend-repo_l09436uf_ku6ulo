"""
Database Schemas for AI Trading Analyst

Each Pydantic model represents a collection in MongoDB. The collection name is the
lowercase of the class name (e.g., User -> "user").
"""
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import datetime

class User(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    role: Literal['trader', 'admin'] = 'trader'
    password: Optional[str] = Field(None, description="Plain for demo only; use hashing in production")
    session_token: Optional[str] = None

class Trade(BaseModel):
    user_id: str = Field(..., description="User identifier (stringified ObjectId)")
    symbol: str
    asset_type: Literal['stock', 'crypto'] = 'stock'
    quantity: float
    price: float
    side: Literal['buy', 'sell']
    timestamp: datetime
    fees: Optional[float] = 0.0
    notes: Optional[str] = None

class Insight(BaseModel):
    user_id: str
    title: str
    message: str
    tags: List[str] = []
    metrics: dict = {}

# Expose schemas to the UI/inspector
class SchemaInfo(BaseModel):
    name: str
    fields: dict

