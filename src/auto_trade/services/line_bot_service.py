"""Line Bot 服務 - 用於發送交易通知和接收命令"""

import os
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

    def send_message(self, message: str) -> bool:
        """發送文字訊息

        Args:
            message: 要發送的訊息

        Returns:
            bool: 發送是否成功
        """
        try:
            self.line_bot_api.push_message(self.user_id, TextSendMessage(text=message))
            return True
        except LineBotApiError as e:
            print(f"❌ Line Bot 發送失敗: {e}")
            return False

    def send_status_message(
        self,
        total_equity: float = None,
        contract: str = None,
        price: int | str = None,
        position: int = None,
        status: str = None,
    ) -> bool:
        """發送狀態訊息

        Args:
        total_equity: 權益總值（可選）
            contract: 合約代碼（可選）
            price: 當前價格（可選）
            position: 持倉數量（可選）
            status: 狀態訊息（可選，用於簡單狀態通知）

        Returns:
            bool: 發送是否成功
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 如果有詳細參數，生成完整啟動訊息
        if contract is not None and price is not None and position is not None:
            # 格式化權益總值
            equity_str = f"{total_equity:,.0f}" if total_equity is not None else "N/A"

            # 使用等寬字體確保對齊
            message = (
                f"ℹ️ Auto Trade Started\n\n"
                f"Time: {timestamp}\n"
                f"Total Equity: {equity_str}\n"
                f"Subscribe: {contract}\n"
                f"Price: {price}\n"
                f"Position: {position}"
            )
        # 否則使用簡單狀態訊息（向後兼容）
        else:
            message = f"""
ℹ️ 系統狀態

時間: {timestamp}
狀態: {status if status else "未知"}
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
        strategy_name: str = "",
        reason: str = "",
    ) -> bool:
        """發送開倉通知訊息"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        direction = "做多" if action in ("Buy", "buy") else "做空"

        message = (
            f"📈 開倉通知\n\n"
            f"策略: {strategy_name}\n"
            f"時間: {timestamp}\n"
            f"商品: {symbol} ({sub_symbol})\n"
            f"方向: {direction}\n"
            f"價格: {price:,.0f}\n"
            f"數量: {quantity} 口\n"
            f"停損: {stop_loss_price:,.0f}"
        )
        if reason:
            message += f"\n原因: {reason}"

        return self.send_message(message)

    def send_close_position_message(
        self,
        symbol: str,
        sub_symbol: str,
        price: float,
        quantity: int,
        exit_reason: str,
        entry_price: float = 0,
        strategy_name: str = "",
    ) -> bool:
        """發送平倉通知訊息"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pnl_pts = price - entry_price if entry_price else 0
        pnl_twd = pnl_pts * quantity * 50
        pnl_sign = "+" if pnl_pts >= 0 else ""

        message = (
            f"📉 平倉通知\n\n"
            f"策略: {strategy_name}\n"
            f"時間: {timestamp}\n"
            f"商品: {symbol} ({sub_symbol})\n"
            f"平倉價格: {price:,.0f}\n"
            f"數量: {quantity} 口\n"
            f"原因: {exit_reason}\n"
            f"盈虧: {pnl_sign}{pnl_pts:,.0f} 點 / {pnl_sign}NT${pnl_twd:,.0f}"
        )

        return self.send_message(message)

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
