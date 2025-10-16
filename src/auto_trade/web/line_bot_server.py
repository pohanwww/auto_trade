#!/usr/bin/env python3
"""Line Bot FastAPI 服務器"""

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from auto_trade.services import LineBotService

# 創建 FastAPI 應用
app = FastAPI(
    title="Auto Trade Line Bot API",
    description="自動交易系統的 Line Bot Webhook 服務",
    version="1.0.0",
)

# 初始化 Line Bot 服務
line_bot_service = LineBotService(
    channel_id=os.environ.get("LINE_CHANNEL_ID"),
    channel_secret=os.environ.get("LINE_CHANNEL_SECRET"),
    messaging_api_token=os.environ.get("LINE_MESSAGING_API_TOKEN"),
)


@app.post("/webhook")
async def webhook(request: Request) -> JSONResponse:
    """Line Bot Webhook 端點"""
    try:
        # 獲取請求內容和簽名
        body = await request.body()
        body_text = body.decode("utf-8")
        signature = request.headers.get("X-Line-Signature")

        if not signature:
            raise HTTPException(
                status_code=400, detail="Missing X-Line-Signature header"
            )

        # 處理 Webhook
        if line_bot_service.handle_webhook(body_text, signature):
            return JSONResponse(content={"status": "OK"})
        else:
            raise HTTPException(status_code=400, detail="Invalid signature")

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Webhook processing failed: {str(e)}"
        ) from e


@app.get("/")
async def root() -> dict[str, str]:
    """根端點"""
    return {"message": "Auto Trade Line Bot API", "status": "running"}


@app.get("/health")
async def health_check() -> dict[str, str]:
    """健康檢查端點"""
    return {"status": "healthy", "service": "line-bot-webhook"}


@app.get("/test")
async def test() -> dict[str, str]:
    """測試端點"""
    return {"message": "Line Bot Webhook 服務器運行中", "framework": "FastAPI"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "line_bot_server:app", host="0.0.0.0", port=port, reload=False, log_level="info"
    )
