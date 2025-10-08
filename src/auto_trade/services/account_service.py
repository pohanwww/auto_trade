"""Account service for managing account-related operations."""

from auto_trade.models import Action, FuturePosition, Margin


class AccountService:
    """帳戶服務類別"""

    def __init__(self, api_client):
        self.api_client = api_client

    def get_margin(self) -> Margin:
        """取得期貨保證金資訊"""
        try:
            # 使用Shioaji API查詢保證金
            margin_data = self.api_client.margin(self.api_client.futopt_account)

            # 轉換為我們的Margin模型
            return Margin(
                yesterday_balance=float(margin_data.yesterday_balance),
                today_balance=float(margin_data.today_balance),
                deposit_withdrawal=float(margin_data.deposit_withdrawal),
                fee=float(margin_data.fee),
                tax=float(margin_data.tax),
                initial_margin=float(margin_data.initial_margin),
                maintenance_margin=float(margin_data.maintenance_margin),
                margin_call=float(margin_data.margin_call),
                risk_indicator=float(margin_data.risk_indicator),
                royalty_revenue_expenditure=float(
                    margin_data.royalty_revenue_expenditure
                ),
                equity=float(margin_data.equity),
                equity_amount=float(margin_data.equity_amount),
                option_openbuy_market_value=float(
                    margin_data.option_openbuy_market_value
                ),
                option_opensell_market_value=float(
                    margin_data.option_opensell_market_value
                ),
                option_open_position=float(margin_data.option_open_position),
                option_settle_profitloss=float(margin_data.option_settle_profitloss),
                future_open_position=float(margin_data.future_open_position),
                today_future_open_position=float(
                    margin_data.today_future_open_position
                ),
                future_settle_profitloss=float(margin_data.future_settle_profitloss),
                available_margin=float(margin_data.available_margin),
                plus_margin=float(margin_data.plus_margin),
                plus_margin_indicator=float(margin_data.plus_margin_indicator),
                security_collateral_amount=float(
                    margin_data.security_collateral_amount
                ),
                order_margin_premium=float(margin_data.order_margin_premium),
                collateral_amount=float(margin_data.collateral_amount),
            )
        except Exception:
            # 如果查詢失敗，返回空值的Margin
            return Margin(
                yesterday_balance=0.0,
                today_balance=0.0,
                deposit_withdrawal=0.0,
                fee=0.0,
                tax=0.0,
                initial_margin=0.0,
                maintenance_margin=0.0,
                margin_call=0.0,
                risk_indicator=0.0,
                royalty_revenue_expenditure=0.0,
                equity=0.0,
                equity_amount=0.0,
                option_openbuy_market_value=0.0,
                option_opensell_market_value=0.0,
                option_open_position=0.0,
                option_settle_profitloss=0.0,
                future_open_position=0.0,
                today_future_open_position=0.0,
                future_settle_profitloss=0.0,
                available_margin=0.0,
                plus_margin=0.0,
                plus_margin_indicator=0.0,
                security_collateral_amount=0.0,
                order_margin_premium=0.0,
                collateral_amount=0.0,
            )

    def get_future_positions(self) -> list[FuturePosition]:
        """取得期貨持倉資訊"""
        try:
            # 使用Shioaji API查詢期貨持倉
            positions = self.api_client.list_positions(self.api_client.futopt_account)

            # 轉換為我們的FuturePosition模型
            future_positions = []
            for pos in positions:
                future_positions.append(
                    FuturePosition(
                        id=pos.id,
                        code=pos.code,
                        direction=Action.Buy
                        if pos.direction.value == "Buy"
                        else Action.Sell,
                        quantity=pos.quantity,
                        price=float(pos.price),
                        last_price=float(pos.last_price),
                        pnl=float(pos.pnl),
                    )
                )

            return future_positions
        except Exception:
            # 如果查詢失敗，返回空列表
            return []
