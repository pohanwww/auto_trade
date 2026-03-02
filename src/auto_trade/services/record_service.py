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
        strategy_name: str = "default",
        record_file: str | None = None,
    ):
        """初始化

        Args:
            strategy_name: 策略名稱，用於區分不同策略的記錄檔和 Google Sheets 標記
            record_file: 本地記錄文件路徑（若未指定則依策略名稱自動生成）
        """
        self.strategy_name = strategy_name
        self.record_file = Path(
            record_file or f"data/state/{strategy_name}/position.json"
        )
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
        """確保記錄文件和目錄存在（自動建立）"""
        self.record_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.record_file.exists():
            self.record_file.write_text("{}")

    # ==================== 本地持倉記錄 ====================

    def save_position(self, record: PositionRecord):
        """保存持倉記錄（僅寫入 position.json，不寫 Google Sheets）

        Args:
            record: 持倉記錄
        """
        try:
            records = self._load_records(self.record_file)
            records[record.sub_symbol] = record.to_dict()
            self.record_file.write_text(
                json.dumps(records, indent=2, ensure_ascii=False)
            )
            print(f"持倉記錄已保存: {record.sub_symbol}")
        except Exception as e:
            print(f"保存持倉記錄失敗: {e}")

    def log_legs_open(
        self,
        record: PositionRecord,
        legs: list[dict],
    ) -> dict[str, int]:
        """為每個 leg 在 Google Sheets 各建一行開倉記錄

        Args:
            record: 持倉記錄（提供 symbol、timeframe 等共用資訊）
            legs: list of dict, 每個 dict 含 leg_id, quantity, entry_price

        Returns:
            dict[str, int]: {leg_id: sheets_row_number}
        """
        row_map: dict[str, int] = {}
        for leg in legs:
            row_number = self.log_trade_open(
                trade_date=datetime.now(),
                symbol=record.sub_symbol,
                timeframe=record.timeframe,
                direction=record.direction.value,
                quantity=leg["quantity"],
                entry_price=leg["entry_price"],
                stop_loss_price=record.stop_loss_price,
            )
            if row_number:
                row_map[leg["leg_id"]] = row_number
        return row_map

    def log_leg_close(
        self,
        leg_id: str,
        row_number: int,
        exit_price: float,
        exit_reason: ExitReason,
        strategy_params: dict | None = None,
    ):
        """更新單一 leg 的 Google Sheets 平倉記錄

        Args:
            leg_id: leg 識別碼
            row_number: Google Sheets 行號
            exit_price: 出場價格
            exit_reason: 出場原因
            strategy_params: 策略參數
        """
        self.log_trade_close(
            row_number=row_number,
            exit_price=exit_price,
            exit_reason=exit_reason,
            strategy_params=strategy_params,
        )
        print(f"✅ Leg {leg_id} 平倉記錄已更新 (row {row_number})")

    def get_position(self, sub_symbol: str) -> PositionRecord | None:
        """獲取持倉記錄

        Args:
            sub_symbol: 子商品代碼

        Returns:
            持倉記錄，如果不存在則返回 None
        """
        try:
            records = self._load_records(self.record_file)

            if sub_symbol in records:
                return PositionRecord.from_dict(records[sub_symbol])

            return None

        except Exception as e:
            print(f"讀取持倉記錄失敗: {e}")
            return None

    def remove_position(self, sub_symbol: str):
        """移除本地持倉記錄（Google Sheets 的更新由 log_leg_close 處理）

        Args:
            sub_symbol: 子商品代碼
        """
        try:
            records = self._load_records(self.record_file)

            if sub_symbol in records:
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
            records = self._load_records(self.record_file)

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
            records = self._load_records(self.record_file)
            return [PositionRecord.from_dict(data) for data in records.values()]
        except Exception as e:
            print(f"列出持倉記錄失敗: {e}")
            return []

    def _remove_position_without_log(self, sub_symbol: str):
        """移除持倉記錄但不記錄到 Google Sheets 交易記錄（用於清理不同步的記錄）

        Args:
            sub_symbol: 子商品代碼
        """
        try:
            records = self._load_records(self.record_file)

            if sub_symbol in records:
                del records[sub_symbol]
                self.record_file.write_text(
                    json.dumps(records, indent=2, ensure_ascii=False)
                )
                print(f"持倉記錄已清理: {sub_symbol}")

        except Exception as e:
            print(f"清理持倉記錄失敗: {e}")

    def _load_records(self, file_path: Path) -> dict:
        """載入記錄

        Args:
            file_path: 文件路徑

        Returns:
            記錄字典
        """
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

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
            worksheet = self._get_or_create_worksheet(self.strategy_name)

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

            # 策略名稱（20 欄）- 開倉時先寫入策略名稱，平倉時會追加參數
            strategy_info = self.strategy_name

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
            worksheet = self._get_or_create_worksheet(self.strategy_name)

            # 更新出場價格（P 欄）
            worksheet.update_cell(row_number, 16, exit_price)

            # 更新出場原因（Q 欄）
            worksheet.update_cell(row_number, 17, exit_reason.value)

            # 更新盈虧公式（R 欄和 S 欄）
            if exit_reason != ExitReason.HOLD:
                pnl_formula = f'=IF(M{row_number}="Buy",P{row_number}-N{row_number},N{row_number}-P{row_number})'
                worksheet.update_cell(row_number, 18, pnl_formula)

                twd_formula = (
                    f'=K{row_number}*R{row_number}'
                    f'*IF(LEFT(J{row_number},3)="MXF",50,200)'
                )
                worksheet.update_cell(row_number, 19, twd_formula)

            # 更新策略參數（T 欄）- 追加出場參數到策略名稱後
            if strategy_params:
                strategy_info = (
                    f"{self.strategy_name} | "
                    f"SL:{strategy_params.get('stop_loss_points', 0)} "
                    f"TS啟動:{strategy_params.get('start_trailing_stop_points', 0)} "
                    f"TS:{strategy_params.get('trailing_stop_points', 0)} "
                    f"TP:{strategy_params.get('take_profit_points', 0)}"
                )
                worksheet.update_cell(row_number, 20, strategy_info)

            print(f"✅ 平倉記錄已更新 Google Sheets 第 {row_number} 行")

        except Exception as e:
            print(f"❌ 更新平倉記錄失敗: {e}")

    def get_latest_row_data(self, worksheet_title: str | None = None) -> dict | None:
        """獲取 Google Sheet 最新行數據

        Args:
            worksheet_title: 工作表名稱，預設為策略名稱

        Returns:
            dict: 最新行的數據字典，如果沒有數據則返回 None
        """
        if not self.sheets_service:
            print("⚠️ Google Sheets 服務未啟用")
            return None

        title = worksheet_title or self.strategy_name

        try:
            import gspread

            # 取得工作表
            worksheet = self.spreadsheet.worksheet(title)

            # 獲取所有數據
            all_values = worksheet.get_all_values()

            if not all_values:
                print(f"📊 工作表 '{title}' 沒有數據")
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
