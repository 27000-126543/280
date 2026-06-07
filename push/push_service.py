import json
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from config.settings import PUSH_CONFIG, REDIS_CONFIG, SYSTEM_CONFIG
from common.models import PushRecord, RecommendItem, PushChannel
from common.logger import LoggerManager
import redis


class PushService:
    def __init__(self):
        self.logger = LoggerManager.get_logger("push")
        self.redis_client = redis.Redis(**REDIS_CONFIG)
        self.executor = ThreadPoolExecutor(max_workers=50)
        self.email_config = PUSH_CONFIG["email_config"]
        self.sms_config = PUSH_CONFIG["sms_config"]
        self.cooldown_hours = PUSH_CONFIG["cooldown_hours"]
        self.rate_limits = PUSH_CONFIG["rate_limit"]

    def push_to_user(
        self,
        user_id: str,
        recommendations: List[RecommendItem],
        channel: PushChannel,
        user_contact: Optional[str] = None,
        ab_test_group: Optional[str] = None,
    ) -> PushRecord:
        push_id = str(uuid.uuid4())
        push_record = PushRecord(
            push_id=push_id,
            user_id=user_id,
            channel=channel,
            recommend_items=recommendations,
            ab_test_group=ab_test_group,
        )

        try:
            if not self._check_cooldown(user_id, channel):
                push_record.status = "skipped_cooldown"
                self._save_push_record(push_record)
                return push_record

            if not self._check_rate_limit(channel):
                push_record.status = "skipped_rate_limit"
                self._save_push_record(push_record)
                return push_record

            if channel == PushChannel.EMAIL:
                self._send_email(user_id, user_contact, recommendations, push_id)
            elif channel == PushChannel.IN_APP:
                self._send_in_app(user_id, recommendations, push_id)
            elif channel == PushChannel.SMS:
                self._send_sms(user_id, user_contact, recommendations, push_id)

            push_record.send_time = datetime.now()
            push_record.status = "sent"
            self._update_cooldown(user_id, channel)

        except Exception as e:
            push_record.status = "failed"
            push_record.error_message = str(e)
            LoggerManager.log_error(
                "push", "push_to_user", e, user_id=user_id, details=f"channel={channel.value}"
            )

        self._save_push_record(push_record)
        LoggerManager.log_operation(
            "push",
            "push_to_user",
            user_id=user_id,
            details=f"channel={channel.value}, status={push_record.status}",
        )
        return push_record

    def batch_push(
        self,
        user_recommendations: Dict[str, List[RecommendItem]],
        channel: PushChannel,
        user_contacts: Optional[Dict[str, str]] = None,
        ab_test_groups: Optional[Dict[str, str]] = None,
    ) -> List[PushRecord]:
        records = []
        futures = []

        for user_id, recommendations in user_recommendations.items():
            user_contact = user_contacts.get(user_id) if user_contacts else None
            ab_group = ab_test_groups.get(user_id) if ab_test_groups else None
            future = self.executor.submit(
                self.push_to_user,
                user_id=user_id,
                recommendations=recommendations,
                channel=channel,
                user_contact=user_contact,
                ab_test_group=ab_group,
            )
            futures.append(future)

        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception as e:
                LoggerManager.log_error("push", "batch_push_task", e)

        self.logger.info(f"Batch push completed: {len(records)} records")
        return records

    def _send_email(
        self,
        user_id: str,
        email: Optional[str],
        recommendations: List[RecommendItem],
        push_id: str,
    ):
        if not email:
            raise ValueError("Email address not provided")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"为您精选的个性化推荐 - {datetime.now().strftime('%Y-%m-%d')}"
        msg["From"] = self.email_config["sender_email"]
        msg["To"] = email

        html_content = self._generate_email_html(recommendations, push_id)
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(
            self.email_config["smtp_server"], self.email_config["smtp_port"]
        ) as server:
            server.starttls()
            server.login(
                self.email_config["sender_email"], self.email_config["sender_password"]
            )
            server.send_message(msg)

    def _generate_email_html(self, recommendations: List[RecommendItem], push_id: str) -> str:
        items_html = ""
        for idx, item in enumerate(recommendations):
            tracking_url = f"/track/click?push_id={push_id}&item_id={item.item_id}&idx={idx}"
            items_html += f"""
            <div style="margin-bottom: 20px; padding: 15px; border: 1px solid #eee; border-radius: 8px;">
                <h3 style="margin: 0 0 10px 0;">{item.title}</h3>
                {f'<img src="{item.image_url}" alt="{item.title}" style="max-width: 200px; height: auto;">' if item.image_url else ''}
                <p style="color: #666;">{item.reason or ''}</p>
                <p style="color: #e74c3c; font-size: 18px; font-weight: bold;">
                    ¥{item.price:.2f}
                    {f'<span style="text-decoration: line-through; color: #999; font-size: 14px; margin-left: 10px;">¥{item.original_price:.2f}</span>' if item.original_price and item.original_price > item.price else ''}
                </p>
                <a href="{tracking_url}" target="_blank" style="display: inline-block; padding: 10px 20px; background: #3498db; color: white; text-decoration: none; border-radius: 5px;">
                    立即查看
                </a>
            </div>
            """

        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h1 style="color: #333; text-align: center;">为您精选的推荐</h1>
            <p style="color: #666; text-align: center;">基于您的浏览和购买历史，我们为您精心挑选了以下商品</p>
            {items_html}
            <p style="text-align: center; color: #999; font-size: 12px; margin-top: 30px;">
                如不想接收此类邮件，请<a href="#">退订</a>
            </p>
        </body>
        </html>
        """

    def _send_in_app(
        self, user_id: str, recommendations: List[RecommendItem], push_id: str
    ):
        try:
            in_app_key = f"in_app_message:{user_id}"
            message_data = {
                "push_id": push_id,
                "title": "为您精选的个性化推荐",
                "recommendations": [
                    {
                        "item_id": item.item_id,
                        "item_type": item.item_type.value,
                        "title": item.title,
                        "image_url": item.image_url,
                        "target_url": item.target_url,
                        "price": item.price,
                        "reason": item.reason,
                    }
                    for item in recommendations
                ],
                "created_at": datetime.now().isoformat(),
                "read": False,
            }
            self.redis_client.lpush(in_app_key, json.dumps(message_data))
            self.redis_client.expire(in_app_key, timedelta(days=7))
        except Exception as e:
            raise RuntimeError(f"Failed to send in-app message: {e}")

    def _send_sms(
        self,
        user_id: str,
        phone: Optional[str],
        recommendations: List[RecommendItem],
        push_id: str,
    ):
        if not phone:
            raise ValueError("Phone number not provided")

        if recommendations:
            top_item = recommendations[0]
            sign_name = self.sms_config["sign_name"]
            message = f"{sign_name}尊敬的用户，基于您的喜好为您推荐：{top_item.title}，特价¥{top_item.price:.2f}，点击查看详情。回T退订"
            self.logger.info(f"[SMS Mock] To: {phone}, Message: {message}")

    def _check_cooldown(self, user_id: str, channel: PushChannel) -> bool:
        try:
            key = f"push_cooldown:{channel.value}:{user_id}"
            return not self.redis_client.exists(key)
        except Exception as e:
            LoggerManager.log_error("push", "check_cooldown", e, user_id=user_id)
            return True

    def _update_cooldown(self, user_id: str, channel: PushChannel):
        try:
            key = f"push_cooldown:{channel.value}:{user_id}"
            self.redis_client.setex(key, timedelta(hours=self.cooldown_hours), "1")
        except Exception as e:
            LoggerManager.log_error("push", "update_cooldown", e, user_id=user_id)

    def _check_rate_limit(self, channel: PushChannel) -> bool:
        try:
            key = f"push_rate_limit:{channel.value}:{datetime.now().strftime('%Y%m%d')}"
            current = self.redis_client.incr(key)
            if current == 1:
                self.redis_client.expire(key, timedelta(days=1))
            return current <= self.rate_limits.get(channel.value, float("inf"))
        except Exception as e:
            LoggerManager.log_error("push", "check_rate_limit", e)
            return True

    def _save_push_record(self, record: PushRecord):
        try:
            key = f"push_record:{record.push_id}"
            data = {
                "push_id": record.push_id,
                "user_id": record.user_id,
                "channel": record.channel.value,
                "recommend_items": [
                    {
                        "item_id": item.item_id,
                        "item_type": item.item_type.value,
                        "title": item.title,
                        "predicted_ctr": item.predicted_ctr,
                        "score": item.score,
                    }
                    for item in record.recommend_items
                ],
                "send_time": record.send_time.isoformat() if record.send_time else None,
                "status": record.status,
                "open_time": record.open_time.isoformat() if record.open_time else None,
                "click_time": record.click_time.isoformat()
                if record.click_time
                else None,
                "conversion_time": record.conversion_time.isoformat()
                if record.conversion_time
                else None,
                "conversion_value": record.conversion_value,
                "error_message": record.error_message,
                "ab_test_group": record.ab_test_group,
            }
            self.redis_client.setex(
                key, timedelta(days=SYSTEM_CONFIG["retention_days"]), json.dumps(data)
            )

            user_key = f"user_push_records:{record.user_id}"
            self.redis_client.lpush(user_key, record.push_id)
            self.redis_client.ltrim(user_key, 0, 999)
        except Exception as e:
            LoggerManager.log_error(
                "push", "save_push_record", e, details=f"push_id={record.push_id}"
            )

    def track_open(self, push_id: str):
        try:
            key = f"push_record:{push_id}"
            data = self.redis_client.get(key)
            if data:
                record_data = json.loads(data)
                record_data["open_time"] = datetime.now().isoformat()
                if record_data["status"] == "sent":
                    record_data["status"] = "opened"
                self.redis_client.set(key, json.dumps(record_data))
                LoggerManager.log_operation(
                    "push", "track_open", details=f"push_id={push_id}"
                )
        except Exception as e:
            LoggerManager.log_error("push", "track_open", e, details=f"push_id={push_id}")

    def track_click(self, push_id: str, item_id: str):
        try:
            key = f"push_record:{push_id}"
            data = self.redis_client.get(key)
            if data:
                record_data = json.loads(data)
                record_data["click_time"] = datetime.now().isoformat()
                record_data["clicked_item_id"] = item_id
                if record_data["status"] in ["sent", "opened"]:
                    record_data["status"] = "clicked"
                self.redis_client.set(key, json.dumps(record_data))
                LoggerManager.log_operation(
                    "push", "track_click", details=f"push_id={push_id}, item_id={item_id}"
                )
        except Exception as e:
            LoggerManager.log_error(
                "push", "track_click", e, details=f"push_id={push_id}"
            )

    def track_conversion(self, push_id: str, value: float = 0.0):
        try:
            key = f"push_record:{push_id}"
            data = self.redis_client.get(key)
            if data:
                record_data = json.loads(data)
                record_data["conversion_time"] = datetime.now().isoformat()
                record_data["conversion_value"] = value
                record_data["status"] = "converted"
                self.redis_client.set(key, json.dumps(record_data))
                LoggerManager.log_operation(
                    "push",
                    "track_conversion",
                    details=f"push_id={push_id}, value={value}",
                )
        except Exception as e:
            LoggerManager.log_error(
                "push", "track_conversion", e, details=f"push_id={push_id}"
            )

    def get_push_records(
        self,
        user_id: Optional[str] = None,
        channel: Optional[PushChannel] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[PushRecord]:
        records = []
        try:
            if user_id:
                user_key = f"user_push_records:{user_id}"
                push_ids = self.redis_client.lrange(user_key, 0, limit - 1)
                for push_id in push_ids:
                    key = f"push_record:{push_id.decode() if isinstance(push_id, bytes) else push_id}"
                    data = self.redis_client.get(key)
                    if data:
                        records.append(self._parse_push_record(json.loads(data)))
        except Exception as e:
            LoggerManager.log_error("push", "get_push_records", e, user_id=user_id)
        return records

    def _parse_push_record(self, data: Dict) -> PushRecord:
        return PushRecord(
            push_id=data["push_id"],
            user_id=data["user_id"],
            channel=PushChannel(data["channel"]),
            recommend_items=[
                RecommendItem(
                    item_id=item["item_id"],
                    item_type=RecommendItem(item["item_type"]),
                    title=item["title"],
                    predicted_ctr=item.get("predicted_ctr", 0.0),
                    score=item.get("score", 0.0),
                )
                for item in data["recommend_items"]
            ],
            send_time=datetime.fromisoformat(data["send_time"])
            if data.get("send_time")
            else None,
            status=data["status"],
            open_time=datetime.fromisoformat(data["open_time"])
            if data.get("open_time")
            else None,
            click_time=datetime.fromisoformat(data["click_time"])
            if data.get("click_time")
            else None,
            conversion_time=datetime.fromisoformat(data["conversion_time"])
            if data.get("conversion_time")
            else None,
            conversion_value=data.get("conversion_value", 0.0),
            error_message=data.get("error_message"),
            ab_test_group=data.get("ab_test_group"),
        )
