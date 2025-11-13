"""記錄服務 - 整合本地記錄和 Google Sheets 交易記錄"""

import json
from datetime import datetime
from pathlib import Path

from auto_trade.core.config import Config
from auto_trade.models import ExitReason
from auto_trade.models.position_record import PositionRecord


class RecordService:
    """記錄服務 - 管理本地持倉記錄和 Google Sheets 交易記錄"""

    def __init__(
        self,
        record_file: str = "data/position_records.json",
    ):
        """初始化

        Args:
            record_file: 本地記錄文件路徑（相對於項目根目錄）
        """
        self.record_file = Path(record_file)
        self._ensure_file_exists()

        # 從 Config 讀取 Google Sheets 設定
        config = Config()
        google_credentials_file = config.google_credentials_path
        google_spreadsheet_name = config.google_spreadsheet_name

        # 初始化 Google Sheets（可選）
        self.sheets_service = None
        if google_credentials_file and google_spreadsheet_name:
            try:
                import gspread
                from google.oauth2.service_account import Credentials

                # 設定權限範圍
                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]

                # 使用服務帳號憑證
                credentials = Credentials.from_service_account_file(
                    google_credentials_file, scopes=scopes
                )

                # 建立客戶端
                client = gspread.authorize(credentials)
                self.spreadsheet = client.open(google_spreadsheet_name)
                self.sheets_service = True
                print(f"✅ Google Sheets 已啟用: {google_spreadsheet_name}")

            except Exception as e:
                print(f"⚠️  Google Sheets 未啟用: {e}")
                self.sheets_service = None

    def _ensure_file_exists(self):
        """確保記錄文件和目錄存在"""
        # 創建目錄
        self.record_file.parent.mkdir(parents=True, exist_ok=True)

        # 創建文件
        if not self.record_file.exists():
            self.record_file.write_text("{}")

    # ==================== 本地持倉記錄 ====================

    def save_position(self, record: PositionRecord):
        """保存持倉記錄

        Args:
            record: 持倉記錄
        """
        try:
            # 讀取現有記錄
            records = self._load_records()

            # 使用 sub_symbol 作為 key
            records[record.sub_symbol] = record.to_dict()

            # 保存
            self.record_file.write_text(
                json.dumps(records, indent=2, ensure_ascii=False)
            )
            print(f"持倉記錄已保存: {record.sub_symbol}")

            # 記錄開倉到 Google Sheets，並保存行號
            row_number = self.log_trade_open(
                trade_date=record.entry_time,
                symbol=record.sub_symbol,
                timeframe=record.timeframe,
                direction="Buy",
                quantity=record.quantity,
                entry_price=record.entry_price,
                stop_loss_price=record.stop_loss_price,
            )

            # 更新記錄中的行號
            record.sheets_row_number = row_number
            records[record.sub_symbol] = record.to_dict()
            self.record_file.write_text(
                json.dumps(records, indent=2, ensure_ascii=False)
            )

        except Exception as e:
            print(f"保存持倉記錄失敗: {e}")

    def get_position(self, sub_symbol: str) -> PositionRecord | None:
        """獲取持倉記錄

        Args:
            sub_symbol: 子商品代碼

        Returns:
            持倉記錄，如果不存在則返回 None
        """
        try:
            records = self._load_records()

            if sub_symbol in records:
                return PositionRecord.from_dict(records[sub_symbol])

            return None

        except Exception as e:
            print(f"讀取持倉記錄失敗: {e}")
            return None

    def remove_position(
        self,
        sub_symbol: str,
        exit_price: float,
        exit_reason: ExitReason,
        strategy_params: dict | None = None,
    ):
        """移除持倉記錄並記錄平倉資訊

        Args:
            sub_symbol: 子商品代碼
            exit_price: 出場價格
            exit_reason: 出場原因
            strategy_params: 策略參數字典（包含 stop_loss_points, start_trailing_stop_points, trailing_stop_points, take_profit_points）
        """
        try:
            records = self._load_records()

            if sub_symbol in records:
                # 獲取持倉記錄用於記錄到 Google Sheets
                position_record = PositionRecord.from_dict(records[sub_symbol])

                # 更新 Google Sheets 中的同一筆記錄
                self.log_trade_close(
                    row_number=position_record.sheets_row_number,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    strategy_params=strategy_params,
                )

                # 刪除本地記錄
                del records[sub_symbol]
                self.record_file.write_text(
                    json.dumps(records, indent=2, ensure_ascii=False)
                )
                print(f"持倉記錄已移除: {sub_symbol}")

        except Exception as e:
            print(f"移除持倉記錄失敗: {e}")

    def update_stop_loss(
        self,
        sub_symbol: str,
        stop_loss_price: float,
        trailing_stop_active: bool = False,
    ):
        """更新停損價格

        Args:
            sub_symbol: 子商品代碼
            stop_loss_price: 新的停損價格
            trailing_stop_active: 移動停損是否啟動
        """
        try:
            records = self._load_records()

            if sub_symbol in records:
                records[sub_symbol]["stop_loss_price"] = stop_loss_price
                records[sub_symbol]["trailing_stop_active"] = trailing_stop_active
                self.record_file.write_text(
                    json.dumps(records, indent=2, ensure_ascii=False)
                )

        except Exception as e:
            print(f"更新停損價格失敗: {e}")

    def list_all_positions(self) -> list[PositionRecord]:
        """列出所有持倉記錄

        Returns:
            持倉記錄列表
        """
        try:
            records = self._load_records()
            return [PositionRecord.from_dict(data) for data in records.values()]
        except Exception as e:
            print(f"列出持倉記錄失敗: {e}")
            return []

    def _load_records(self) -> dict:
        """載入所有持倉記錄"""
        try:
            return json.loads(self.record_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _remove_position_without_log(self, sub_symbol: str):
        """移除持倉記錄但不記錄到 Google Sheets 交易記錄（用於清理不同步的記錄）

        Args:
            sub_symbol: 子商品代碼
        """
        try:
            records = self._load_records()

            if sub_symbol in records:
                del records[sub_symbol]
                self.record_file.write_text(
                    json.dumps(records, indent=2, ensure_ascii=False)
                )
                print(f"持倉記錄已清理: {sub_symbol}")

        except Exception as e:
            print(f"清理持倉記錄失敗: {e}")

    # ==================== Google Sheets 記錄 ====================

    def log_trade_open(
        self,
        trade_date: datetime,
        symbol: str,
        timeframe: str,
        direction: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
    ) -> int:
        """記錄開倉到 Google Sheets

        Returns:
            int: 在 Google Sheets 中的行號
        """
        if not self.sheets_service:
            return 0

        try:
            worksheet = self._get_or_create_worksheet("交易記錄")

            # 取得現有資料
            all_values = worksheet.get_all_values()

            # 設定標題（如果工作表為空或第一行不是標題）
            if len(all_values) == 0:
                headers = [
                    "No.",
                    "勝率",
                    "平均盈虧點",
                    "總盈利點",
                    "總虧損點",
                    "總盈虧點",
                    "盈虧比",
                    "總盈虧",
                    "交易日期",
                    "商品",
                    "數量",
                    "時間尺度",
                    "多空",
                    "進場價格",
                    "停損價格",
                    "出場價格",
                    "出場原因",
                    "盈虧（點數）",
                    "盈虧（新台幣）",
                    "策略",
                ]
                worksheet.append_row(headers)
                print("✅ 已創建標題行")
                all_values = worksheet.get_all_values()

            # 取得下一列的行號
            next_row = len(all_values) + 1
            prev_row = next_row - 1

            # 前 8 個統計公式欄位
            if next_row == 2:  # 第一筆資料
                formulas = [
                    "1",  # No: 第一筆
                    '=COUNTIF(R2:R2,">0")/COUNTA(R2:R2)',  # 勝率
                    "=AVERAGE(R2:R2)",  # 平均盈虧點
                    '=SUMIF(R2:R2,">0",R2:R2)',  # 總盈利點
                    '=SUMIF(R2:R2,"<0",R2:R2)',  # 總虧損點
                    "=SUM(R2:R2)",  # 總盈虧點
                    "=ABS(D2/E2)",  # 盈虧比
                    "=SUM(S2:S2)",  # 總盈虧
                ]
            else:
                formulas = [
                    f"=A{prev_row}+1",  # No: 累計編號
                    f'=COUNTIF(R$2:R{next_row},">0")/COUNTA(R$2:R{next_row})',  # 勝率
                    f"=AVERAGE(R$2:R{next_row})",  # 平均盈虧點
                    f'=SUMIF(R$2:R{next_row},">0",R$2:R{next_row})',  # 總盈利點
                    f'=SUMIF(R$2:R{next_row},"<0",R$2:R{next_row})',  # 總虧損點
                    f"=SUM(R$2:R{next_row})",  # 總盈虧點
                    f'=IF(E{next_row}=0,"inf",ABS(D{next_row}/E{next_row}))',  # 盈虧比
                    f"=SUM(S$2:S{next_row})",  # 總盈虧
                ]

            # 資料欄位（9-17 欄）
            data = [
                trade_date.strftime("%Y-%m-%d %H:%M:%S"),  # I 欄：交易日期
                symbol,  # J 欄：商品
                quantity,  # K 欄：數量
                timeframe,  # L 欄：時間尺度
                direction,  # M 欄：多空
                entry_price,  # N 欄：進場價格
                stop_loss_price,  # O 欄：停損價格
                "",  # P 欄：出場價格（開倉時為空）
                ExitReason.HOLD.value,  # Q 欄：出場原因（Hold）
            ]

            # 盈虧公式（18-19 欄）- 開倉時為空
            pnl_formulas = ["", ""]

            # 策略參數（20 欄）- 開倉時為空
            strategy_info = ""

            # 合併：統計公式 + 資料 + 盈虧公式 + 策略
            row = formulas + data + pnl_formulas + [strategy_info]

            worksheet.append_row(row, value_input_option="USER_ENTERED")
            print(f"✅ 開倉記錄已寫入 Google Sheets 第 {next_row} 行")

            return next_row

        except Exception as e:
            print(f"❌ 寫入開倉記錄失敗: {e}")
            return 0

    def log_trade_close(
        self,
        row_number: int,
        exit_price: float,
        exit_reason: ExitReason,
        strategy_params: dict | None = None,
    ):
        """更新 Google Sheets 中的交易記錄為平倉狀態"""
        if not self.sheets_service or not row_number:
            return

        try:
            worksheet = self._get_or_create_worksheet("交易記錄")

            # 更新出場價格（P 欄）
            worksheet.update_cell(row_number, 16, exit_price)

            # 更新出場原因（Q 欄）
            worksheet.update_cell(row_number, 17, exit_reason.value)

            # 更新盈虧公式（R 欄和 S 欄）
            if exit_reason != ExitReason.HOLD:
                # R 欄：盈虧（點數）= 出場價格 - 進場價格（做多）
                pnl_formula = f"=P{row_number}-N{row_number}"
                worksheet.update_cell(row_number, 18, pnl_formula)

                # S 欄：盈虧（新台幣）= 數量 * 盈虧點數 * 50
                twd_formula = f"=K{row_number}*R{row_number}*50"
                worksheet.update_cell(row_number, 19, twd_formula)

            # 更新策略參數（T 欄）
            if strategy_params:
                strategy_info = (
                    f"初始停損:{strategy_params.get('stop_loss_points', 0)} \n"
                    f"啟動移停:{strategy_params.get('start_trailing_stop_points', 0)} \n"
                    f"移停點數:{strategy_params.get('trailing_stop_points', 0)} \n"
                    f"獲利點數:{strategy_params.get('take_profit_points', 0)} \n"
                )
                worksheet.update_cell(row_number, 20, strategy_info)

            print(f"✅ 平倉記錄已更新 Google Sheets 第 {row_number} 行")

        except Exception as e:
            print(f"❌ 更新平倉記錄失敗: {e}")

    def get_latest_row_data(self, worksheet_title: str = "交易記錄") -> dict | None:
        """獲取 Google Sheet 最新行數據

        Args:
            worksheet_title: 工作表名稱，預設為 "交易記錄"

        Returns:
            dict: 最新行的數據字典，如果沒有數據則返回 None
        """
        if not self.sheets_service:
            print("⚠️ Google Sheets 服務未啟用")
            return None

        try:
            import gspread

            # 取得工作表
            worksheet = self.spreadsheet.worksheet(worksheet_title)

            # 獲取所有數據
            all_values = worksheet.get_all_values()

            if not all_values:
                print(f"📊 工作表 '{worksheet_title}' 沒有數據")
                return None

            # 獲取標題行（第一行）
            headers = all_values[0]

            # 獲取最新行數據（最後一行）
            latest_row = all_values[-1]

            # 將標題和數據組合成字典
            latest_data = {}
            for i, header in enumerate(headers):
                if i < len(latest_row):
                    latest_data[header] = latest_row[i]
                else:
                    latest_data[header] = ""

            print(f"✅ 成功獲取工作表 '{worksheet_title}' 最新行數據")
            return latest_data

        except gspread.exceptions.WorksheetNotFound:
            print(f"❌ 工作表 '{worksheet_title}' 不存在")
            return None
        except Exception as e:
            print(f"❌ 獲取最新行數據失敗: {e}")
            return None

    def _get_or_create_worksheet(self, title: str):
        """取得或創建工作表

        Args:
            title: 工作表名稱

        Returns:
            工作表對象
        """
        if not self.sheets_service:
            return None

        try:
            import gspread

            # 嘗試取得現有工作表
            worksheet = self.spreadsheet.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            # 工作表不存在，創建新的（20 欄，1000 行）
            worksheet = self.spreadsheet.add_worksheet(title=title, rows=1000, cols=20)

        return worksheet
