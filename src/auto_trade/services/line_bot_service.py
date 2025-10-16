"""Line Bot 服務 - 用於發送交易通知和接收命令"""

import os
from collections import defaultdict
from datetime import datetime

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    BoxComponent,
    BubbleContainer,
    ButtonComponent,
    FlexSendMessage,
    MessageEvent,
    PostbackAction,
    TextComponent,
    TextMessage,
    TextSendMessage,
)


class LineBotService:
    """Line Bot 服務類"""

    def __init__(self, channel_id: str, channel_secret: str, messaging_api_token: str):
        """初始化 Line Bot 服務

        Args:
            channel_id: Line Bot Channel ID
            channel_secret: Line Bot Channel Secret
            messaging_api_token: Line Bot Messaging API Token
        """
        self.channel_id = channel_id
        self.channel_secret = channel_secret
        self.line_bot_api = LineBotApi(messaging_api_token)
        self.handler = WebhookHandler(channel_secret)
        self.user_id = os.environ.get("LINE_USER_ID")  # 您的 Line User ID

        # 配額管理
        self.daily_quota = 200  # 每天 200 則訊息
        self.monthly_quota = 500  # 每月 500 則訊息
        self.message_count = defaultdict(int)  # 記錄訊息數量
        self.last_reset_date = datetime.now().date()

    def _check_quota(self) -> bool:
        """檢查配額是否足夠

        Returns:
            bool: 是否可以發送訊息
        """
        current_date = datetime.now().date()

        # 檢查是否需要重置每日計數
        if current_date != self.last_reset_date:
            self.message_count["daily"] = 0
            self.last_reset_date = current_date

        # 檢查每日配額
        if self.message_count["daily"] >= self.daily_quota:
            print(f"⚠️ 已達到每日配額限制: {self.daily_quota}")
            return False

        # 檢查每月配額
        if self.message_count["monthly"] >= self.monthly_quota:
            print(f"⚠️ 已達到每月配額限制: {self.monthly_quota}")
            return False

        return True

    def _update_quota(self):
        """更新配額計數"""
        self.message_count["daily"] += 1
        self.message_count["monthly"] += 1

        # 顯示配額使用情況
        daily_remaining = self.daily_quota - self.message_count["daily"]
        monthly_remaining = self.monthly_quota - self.message_count["monthly"]

        print(f"📊 配額使用: 每日剩餘 {daily_remaining}, 每月剩餘 {monthly_remaining}")

    def send_message(self, message: str) -> bool:
        """發送文字訊息

        Args:
            message: 要發送的訊息

        Returns:
            bool: 發送是否成功
        """
        # 檢查配額
        if not self._check_quota():
            return False

        try:
            self.line_bot_api.push_message(self.user_id, TextSendMessage(text=message))
            self._update_quota()
            return True
        except LineBotApiError as e:
            print(f"❌ Line Bot 發送失敗: {e}")
            return False

    def send_status_message(self, status: str) -> bool:
        """發送狀態訊息

        Args:
            status: 狀態訊息

        Returns:
            bool: 發送是否成功
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        message = f"""
ℹ️ 系統狀態

時間: {timestamp}
狀態: {status}
        """

        return self.send_message(message.strip())

    def send_open_position_message(
        self,
        symbol: str,
        sub_symbol: str,
        price: float,
        quantity: int,
        action: str,
        stop_loss_price: float,
    ) -> bool:
        """發送開倉通知訊息

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            price: 開倉價格
            quantity: 數量
            action: 交易方向
            stop_loss_price: 停損價格

        Returns:
            bool: 發送是否成功
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        message = f"""
📈 開倉通知

時間: {timestamp}
商品: {symbol} ({sub_symbol})
開倉價格: {price:,.1f}
數量: {quantity}
方向: {action}
停損價格: {stop_loss_price:,.1f}
"""

        return self.send_message(message.strip())

    def send_close_position_message(
        self,
        symbol: str,
        sub_symbol: str,
        price: float,
        exit_reason: str,
        latest_data: dict,
    ) -> bool:
        """發送平倉通知訊息

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            price: 平倉價格
            exit_reason: 平倉原因
            latest_data: Google Sheets 最新數據

        Returns:
            bool: 發送是否成功
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        message = f"""
📉 平倉通知

時間: {timestamp}
商品: {symbol} ({sub_symbol})
平倉價格: {price:,.1f}
平倉原因: {exit_reason}

📊 Google Sheets 最新記錄:
"""
        for key, value in latest_data.items():
            message += f"{key}: {value}\n"

        return self.send_message(message.strip())

    def create_control_menu(self) -> FlexSendMessage:
        """創建控制選單

        Returns:
            FlexSendMessage: 控制選單訊息
        """
        bubble = BubbleContainer(
            body=BoxComponent(
                layout="vertical",
                contents=[
                    TextComponent(
                        text="🤖 交易控制台", weight="bold", size="xl", color="#1DB446"
                    ),
                    TextComponent(
                        text="選擇要執行的操作", size="sm", color="#666666", margin="md"
                    ),
                ],
            ),
            footer=BoxComponent(
                layout="vertical",
                contents=[
                    ButtonComponent(
                        style="primary",
                        color="#1DB446",
                        text="📊 查看狀態",
                        action=PostbackAction(label="查看狀態", data="action=status"),
                    ),
                    ButtonComponent(
                        style="secondary",
                        text="⏸️ 暫停交易",
                        action=PostbackAction(label="暫停交易", data="action=pause"),
                    ),
                    ButtonComponent(
                        style="secondary",
                        text="▶️ 恢復交易",
                        action=PostbackAction(label="恢復交易", data="action=resume"),
                    ),
                    ButtonComponent(
                        style="secondary",
                        text="📈 回測報告",
                        action=PostbackAction(label="回測報告", data="action=backtest"),
                    ),
                ],
            ),
        )

        return FlexSendMessage(alt_text="交易控制台", contents=bubble)

    def handle_webhook(self, body: str, signature: str) -> bool:
        """處理 Webhook 事件

        Args:
            body: 請求內容
            signature: 簽名

        Returns:
            bool: 處理是否成功
        """
        try:
            self.handler.handle(body, signature)
            return True
        except InvalidSignatureError:
            print("❌ Line Bot 簽名驗證失敗")
            return False
        except Exception as e:
            print(f"❌ Line Bot Webhook 處理失敗: {e}")
            return False

    def register_message_handler(self, handler_func):
        """註冊訊息處理器

        Args:
            handler_func: 處理函數
        """
        self.handler.add(MessageEvent, message=TextMessage)(handler_func)
