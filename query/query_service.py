import json
import csv
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
from config.settings import REDIS_CONFIG, SYSTEM_CONFIG, REPORT_CONFIG
from common.models import PushChannel
from common.logger import LoggerManager
import redis


class QueryService:
    def __init__(self):
        self.logger = LoggerManager.get_logger("query")
        self.redis_client = redis.Redis(**REDIS_CONFIG)
        self.export_dir = os.path.join(REPORT_CONFIG["output_dir"], "exports")
        os.makedirs(self.export_dir, exist_ok=True)

    def query_recommendation_details(
        self,
        user_id: Optional[str] = None,
        channel: Optional[PushChannel] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> List[Dict]:
        results = []
        try:
            if user_id:
                push_records_key = f"user_push_records:{user_id}"
                push_ids = self.redis_client.lrange(push_records_key, offset, offset + limit - 1)

                for push_id in push_ids:
                    push_id_str = push_id.decode() if isinstance(push_id, bytes) else push_id
                    push_key = f"push_record:{push_id_str}"
                    push_data = self.redis_client.get(push_key)
                    if push_data:
                        record = json.loads(push_data)

                        if channel and record.get("channel") != channel.value:
                            continue

                        send_time_str = record.get("send_time")
                        if send_time_str:
                            send_time = datetime.fromisoformat(send_time_str)
                            if start_time and send_time < start_time:
                                continue
                            if end_time and send_time > end_time:
                                continue

                        results.append(record)
            else:
                all_keys = self.redis_client.scan_iter(match="push_record:*", count=limit)
                count = 0
                for key in all_keys:
                    if count >= limit:
                        break

                    key_str = key.decode() if isinstance(key, bytes) else key
                    push_data = self.redis_client.get(key_str)
                    if push_data:
                        record = json.loads(push_data)

                        if channel and record.get("channel") != channel.value:
                            continue

                        send_time_str = record.get("send_time")
                        if send_time_str:
                            send_time = datetime.fromisoformat(send_time_str)
                            if start_time and send_time < start_time:
                                continue
                            if end_time and send_time > end_time:
                                continue

                        results.append(record)
                        count += 1

            LoggerManager.log_operation(
                "query",
                "query_recommendation_details",
                details=f"user_id={user_id}, channel={channel.value if channel else 'all'}, count={len(results)}",
            )
        except Exception as e:
            LoggerManager.log_error("query", "query_recommendation_details", e)
        return results

    def query_user_profile(self, user_id: str) -> Optional[Dict]:
        try:
            key = f"user_profile:{user_id}"
            profile_data = self.redis_client.get(key)
            if profile_data:
                return json.loads(profile_data)
        except Exception as e:
            LoggerManager.log_error("query", "query_user_profile", e, user_id=user_id)
        return None

    def query_user_behavior(
        self,
        user_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        behaviors = []
        try:
            key = f"user_behavior:{user_id}"
            behavior_data = self.redis_client.lrange(key, 0, limit - 1)

            for data in behavior_data:
                behavior = json.loads(data)
                timestamp_str = behavior.get("timestamp")
                if timestamp_str:
                    timestamp = datetime.fromisoformat(timestamp_str)
                    if start_time and timestamp < start_time:
                        continue
                    if end_time and timestamp > end_time:
                        continue
                behaviors.append(behavior)
        except Exception as e:
            LoggerManager.log_error("query", "query_user_behavior", e, user_id=user_id)
        return behaviors

    def export_to_csv(
        self,
        data: List[Dict],
        filename: str,
        columns: Optional[List[str]] = None,
    ) -> str:
        try:
            if not filename.endswith(".csv"):
                filename += ".csv"

            filepath = os.path.join(self.export_dir, filename)

            if not data:
                with open(filepath, "w", encoding="utf-8-sig") as f:
                    pass
                return filepath

            if columns is None:
                columns = list(data[0].keys())

            df = pd.DataFrame(data)
            df = df[columns] if all(col in df.columns for col in columns) else df
            df.to_csv(filepath, index=False, encoding="utf-8-sig")

            LoggerManager.log_operation(
                "query", "export_to_csv", details=f"filename={filename}, rows={len(data)}"
            )
            return filepath
        except Exception as e:
            LoggerManager.log_error("query", "export_to_csv", e)
            return ""

    def export_to_excel(
        self,
        data: List[Dict],
        filename: str,
        sheet_name: str = "Sheet1",
        columns: Optional[List[str]] = None,
    ) -> str:
        try:
            if not filename.endswith(".xlsx"):
                filename += ".xlsx"

            filepath = os.path.join(self.export_dir, filename)

            if not data:
                pd.DataFrame().to_excel(filepath, index=False)
                return filepath

            df = pd.DataFrame(data)
            if columns and all(col in df.columns for col in columns):
                df = df[columns]

            df.to_excel(filepath, sheet_name=sheet_name, index=False, engine="openpyxl")

            LoggerManager.log_operation(
                "query", "export_to_excel", details=f"filename={filename}, rows={len(data)}"
            )
            return filepath
        except Exception as e:
            LoggerManager.log_error("query", "export_to_excel", e)
            return ""

    def batch_export_recommendations(
        self,
        user_ids: Optional[List[str]] = None,
        channel: Optional[PushChannel] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        export_format: str = "excel",
    ) -> str:
        try:
            all_data = []
            limit_per_user = 100

            if user_ids:
                for user_id in user_ids:
                    user_data = self.query_recommendation_details(
                        user_id=user_id,
                        channel=channel,
                        start_time=start_time,
                        end_time=end_time,
                        limit=limit_per_user,
                    )
                    all_data.extend(user_data)
            else:
                all_data = self.query_recommendation_details(
                    channel=channel,
                    start_time=start_time,
                    end_time=end_time,
                    limit=10000,
                )

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"recommendation_export_{timestamp}"

            if export_format.lower() == "csv":
                return self.export_to_csv(all_data, filename)
            else:
                return self.export_to_excel(all_data, filename)

        except Exception as e:
            LoggerManager.log_error("query", "batch_export_recommendations", e)
            return ""

    def get_export_files(self) -> List[Dict]:
        files = []
        try:
            for filename in os.listdir(self.export_dir):
                filepath = os.path.join(self.export_dir, filename)
                if os.path.isfile(filepath):
                    stat = os.stat(filepath)
                    files.append(
                        {
                            "filename": filename,
                            "size": stat.st_size,
                            "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                            "filepath": filepath,
                        }
                    )
            files.sort(key=lambda x: x["created_at"], reverse=True)
        except Exception as e:
            LoggerManager.log_error("query", "get_export_files", e)
        return files

    def query_statistics_summary(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        channel: Optional[PushChannel] = None,
    ) -> Dict:
        summary = {
            "total_impressions": 0,
            "total_clicks": 0,
            "total_conversions": 0,
            "total_revenue": 0.0,
            "avg_ctr": 0.0,
            "avg_cvr": 0.0,
        }

        try:
            if start_time is None:
                start_time = datetime.now() - timedelta(days=30)
            if end_time is None:
                end_time = datetime.now()

            current = start_time
            day_count = 0

            while current <= end_time:
                date_key = current.strftime("%Y%m%d")
                channel_key = channel.value if channel else "total"

                impressions = self.redis_client.get(f"stats:impressions:{date_key}:{channel_key}") or 0
                clicks = self.redis_client.get(f"stats:clicks:{date_key}:{channel_key}") or 0
                conversions = self.redis_client.get(f"stats:conversions:{date_key}:{channel_key}") or 0
                revenue = self.redis_client.get(f"stats:revenue:{date_key}:{channel_key}") or 0

                summary["total_impressions"] += int(impressions) if impressions else 0
                summary["total_clicks"] += int(clicks) if clicks else 0
                summary["total_conversions"] += int(conversions) if conversions else 0
                summary["total_revenue"] += float(revenue) if revenue else 0.0

                day_count += 1
                current += timedelta(days=1)

            if summary["total_impressions"] > 0:
                summary["avg_ctr"] = summary["total_clicks"] / summary["total_impressions"]
            if summary["total_clicks"] > 0:
                summary["avg_cvr"] = summary["total_conversions"] / summary["total_clicks"]

            summary["start_date"] = start_time.strftime("%Y-%m-%d")
            summary["end_date"] = end_time.strftime("%Y-%m-%d")
            summary["channel"] = channel.value if channel else "all"

        except Exception as e:
            LoggerManager.log_error("query", "query_statistics_summary", e)
        return summary
