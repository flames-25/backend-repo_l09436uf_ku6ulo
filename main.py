import os
from io import StringIO
from typing import List, Optional
from datetime import datetime
import csv

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import User, Trade, Insight

app = FastAPI(title="AI Trading Analyst API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    email: str
    password: str

class LoginResponse(BaseModel):
    token: str
    role: str
    email: str


def _object_id_or_none(val: str):
    try:
        return ObjectId(val)
    except Exception:
        return None


def _find_user_by_token(token: str):
    if not db:
        return None
    # try by session token string
    user = db["user"].find_one({"session_token": token})
    if user:
        return user
    # try by ObjectId
    oid = _object_id_or_none(token)
    if oid:
        user = db["user"].find_one({"_id": oid})
        if user:
            return user
    return None


@app.get("/")
def read_root():
    return {"message": "AI Trading Analyst Backend Running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    user = db["user"].find_one({"email": payload.email}) if db else None
    if not user:
        # auto-create for demo
        new_user = User(email=payload.email, name=payload.email.split("@")[0])
        inserted_id = create_document("user", new_user)
        token = inserted_id
        try:
            db["user"].update_one({"_id": ObjectId(inserted_id)}, {"$set": {"session_token": token}})
        except Exception:
            pass
        return LoginResponse(token=token, role=new_user.role, email=new_user.email)

    token = str(user.get("session_token") or user.get("_id"))
    return LoginResponse(token=token, role=user.get("role", "trader"), email=user.get("email"))


@app.post("/trades/upload")
def upload_trades(file: UploadFile = File(...), user_token: str = Form(...)):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")

    user = _find_user_by_token(user_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid user token")

    try:
        content = file.file.read().decode("utf-8")
        reader = csv.DictReader(StringIO(content))
        rows = list(reader)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV file")

    required_cols = {"symbol", "asset_type", "quantity", "price", "side", "timestamp"}
    if not rows or not required_cols.issubset(set(rows[0].keys())):
        missing = required_cols - set(rows[0].keys() if rows else [])
        raise HTTPException(status_code=400, detail=f"Missing columns: {', '.join(missing)}")

    inserted = 0
    for r in rows:
        try:
            rec = {
                "user_id": str(user["_id"]),
                "symbol": r["symbol"],
                "asset_type": (r.get("asset_type") or "stock").lower(),
                "quantity": float(r["quantity"]),
                "price": float(r["price"]),
                "side": (r["side"]).lower(),
                "timestamp": datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).isoformat(),
                "fees": float(r.get("fees", 0) or 0),
                "notes": r.get("notes") or None,
            }
            create_document("trade", rec)
            inserted += 1
        except Exception:
            continue

    return {"status": "ok", "inserted": inserted}


def compute_metrics(trades: list):
    # aggregate daily pnl by date
    daily = {}
    for t in trades:
        ts = t.get("timestamp")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        day = dt.date().isoformat()
        qty = float(t.get("quantity", 0) or 0)
        price = float(t.get("price", 0) or 0)
        side = (t.get("side", "buy")).lower()
        sign = 1 if side == "buy" else -1
        notional = price * qty * sign
        daily[day] = daily.get(day, 0.0) + notional

    days = sorted(daily.keys())
    pnl_series = [daily[d] for d in days]

    total_return = sum(pnl_series)
    wins = sum(1 for v in pnl_series if v > 0)
    win_rate = (wins / len(pnl_series) * 100.0) if pnl_series else 0.0

    mean = (total_return / len(pnl_series)) if pnl_series else 0.0
    variance = (sum((v - mean) ** 2 for v in pnl_series) / (len(pnl_series) - 1)) if len(pnl_series) > 1 else 0.0
    std = variance ** 0.5
    sharpe = (mean / std) if std > 1e-9 else 0.0

    # max drawdown on cumulative pnl
    cum = 0.0
    peak = float('-inf')
    max_dd = 0.0
    for v in pnl_series:
        cum += v
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    metrics = {
        "total_return": round(float(total_return), 4),
        "win_rate": round(float(win_rate), 2),
        "volatility": round(float(std), 4),
        "sharpe": round(float(sharpe), 4),
        "max_drawdown": round(float(max_dd), 4),
    }

    daily_list = [{"timestamp": d, "pnl": daily[d]} for d in days]
    return metrics, daily_list


@app.get("/portfolio/summary")
def portfolio_summary(user_token: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")
    user = _find_user_by_token(user_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid user token")

    trades = get_documents("trade", {"user_id": str(user["_id"])})
    if not trades:
        return {"metrics": {"total_return": 0, "win_rate": 0, "volatility": 0, "sharpe": 0, "max_drawdown": 0}, "daily": []}

    metrics, daily = compute_metrics(trades)
    return {"metrics": metrics, "daily": daily}


@app.get("/insights")
def ai_insights(user_token: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")
    user = _find_user_by_token(user_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid user token")

    trades = get_documents("trade", {"user_id": str(user["_id"])})
    if not trades:
        return {"insights": []}

    metrics, daily = compute_metrics(trades)

    # Simple projection: next-day PnL = average of last N days (N=3)
    N = 3
    last = [d["pnl"] for d in daily[-N:]]
    forecast = round(sum(last) / len(last), 4) if last else None

    message = (
        f"Your win rate is {metrics['win_rate']}%. "
        f"Estimated Sharpe {metrics['sharpe']}. "
        f"Max drawdown observed {metrics['max_drawdown']}. "
        + (f"Model projects next-day PnL around {forecast:.2f}." if forecast is not None else "Insufficient data for projection.")
    )

    insight = Insight(
        user_id=str(user["_id"]),
        title="Daily Risk & Trend Overview",
        message=message,
        tags=["risk", "trend", "forecast"],
        metrics={"risk_exposure": metrics["volatility"], **metrics, "forecast_pnl": forecast}
    )

    try:
        create_document("insight", insight)
    except Exception:
        pass

    return {"insights": [insight.model_dump()]}


@app.get("/schema")
def get_schema():
    # Expose schema models for the viewer
    return {
        "models": {
            "user": User.model_json_schema(),
            "trade": Trade.model_json_schema(),
            "insight": Insight.model_json_schema(),
        }
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
