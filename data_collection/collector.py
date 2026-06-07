import json
import uuid
from datetime import datetime
from typing import List, Dict
from kafka import KafkaProducer
from config.settings import KAFKA_CONFIG, SYSTEM_CONFIG
from common.models import UserBehaviorEvent, BehaviorType, ChannelType
from common.logger import LoggerManager


class DataCollector:
    def __init__(self):
        self.logger = LoggerManager.get_logger("data_collection")
        self.producer = None
        self._init_kafka()

    def _init_kafka(self):
        try:
            self.producer = KafkaProducer(
                bootstrap_servers=KAFKA_CONFIG["bootstrap_servers"],
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                max_request_size=10 * 1024 * 1024,
                linger_ms=50,
                batch_size=65536,
                acks=1,
            )
            self.logger.info("Kafka producer initialized successfully")
        except Exception as e:
            LoggerManager.log_error(
                "data_collection", "init_kafka", e, details="Kafka connection failed"
            )

    def collect_event(
        self,
        user_id: str,
        behavior_type: BehaviorType,
        channel: ChannelType,
        item_id: str = None,
        item_type: str = None,
        category: str = None,
        session_id: str = None,
        page_url: str = None,
        referrer: str = None,
        ip_address: str = None,
        user_agent: str = None,
        duration: float = None,
        price: float = None,
        quantity: int = None,
        extra: Dict = None,
    ) -> str:
        event = UserBehaviorEvent(
            event_id=str(uuid.uuid4()),
            user_id=user_id,
            behavior_type=behavior_type,
            channel=channel,
            item_id=item_id,
            item_type=item_type,
            category=category,
            timestamp=datetime.now(),
            session_id=session_id or str(uuid.uuid4()),
            page_url=page_url,
            referrer=referrer,
            ip_address=ip_address,
            user_agent=user_agent,
            duration=duration,
            price=price,
            quantity=quantity,
            extra=extra or {},
        )
        self._send_to_kafka(event)
        LoggerManager.log_operation(
            "data_collection",
            "collect_event",
            user_id=user_id,
            details=f"type={behavior_type.value}, channel={channel.value}",
        )
        return event.event_id

    def _send_to_kafka(self, event: UserBehaviorEvent):
        try:
            if self.producer:
                event_dict = {
                    "event_id": event.event_id,
                    "user_id": event.user_id,
                    "behavior_type": event.behavior_type.value,
                    "channel": event.channel.value,
                    "item_id": event.item_id,
                    "item_type": event.item_type,
                    "category": event.category,
                    "timestamp": event.timestamp.isoformat(),
                    "session_id": event.session_id,
                    "page_url": event.page_url,
                    "referrer": event.referrer,
                    "ip_address": event.ip_address,
                    "user_agent": event.user_agent,
                    "duration": event.duration,
                    "price": event.price,
                    "quantity": event.quantity,
                    "extra": event.extra,
                }
                self.producer.send(
                    KAFKA_CONFIG["topics"]["user_behavior"],
                    value=event_dict,
                    key=event.user_id.encode("utf-8"),
                )
        except Exception as e:
            LoggerManager.log_error(
                "data_collection",
                "send_to_kafka",
                e,
                user_id=event.user_id,
                details=f"event_id={event.event_id}",
            )

    def batch_collect(self, events: List[Dict]) -> List[str]:
        event_ids = []
        batch_size = SYSTEM_CONFIG["batch_size"]
        for i in range(0, len(events), batch_size):
            batch = events[i : i + batch_size]
            for event_data in batch:
                try:
                    event_id = self.collect_event(
                        user_id=event_data["user_id"],
                        behavior_type=BehaviorType(event_data["behavior_type"]),
                        channel=ChannelType(event_data["channel"]),
                        item_id=event_data.get("item_id"),
                        item_type=event_data.get("item_type"),
                        category=event_data.get("category"),
                        session_id=event_data.get("session_id"),
                        page_url=event_data.get("page_url"),
                        referrer=event_data.get("referrer"),
                        ip_address=event_data.get("ip_address"),
                        user_agent=event_data.get("user_agent"),
                        duration=event_data.get("duration"),
                        price=event_data.get("price"),
                        quantity=event_data.get("quantity"),
                        extra=event_data.get("extra"),
                    )
                    event_ids.append(event_id)
                except Exception as e:
                    LoggerManager.log_error(
                        "data_collection",
                        "batch_collect",
                        e,
                        details=f"event_data={event_data}",
                    )
        self.logger.info(f"Batch collected {len(event_ids)} events")
        return event_ids

    def collect_website_event(self, user_id: str, behavior_type: BehaviorType, **kwargs):
        return self.collect_event(
            user_id=user_id,
            behavior_type=behavior_type,
            channel=ChannelType.WEBSITE,
            **kwargs,
        )

    def collect_app_event(self, user_id: str, behavior_type: BehaviorType, **kwargs):
        return self.collect_event(
            user_id=user_id,
            behavior_type=behavior_type,
            channel=ChannelType.APP,
            **kwargs,
        )

    def collect_miniprogram_event(self, user_id: str, behavior_type: BehaviorType, **kwargs):
        return self.collect_event(
            user_id=user_id,
            behavior_type=behavior_type,
            channel=ChannelType.MINIPROGRAM,
            **kwargs,
        )

    def close(self):
        if self.producer:
            self.producer.flush()
            self.producer.close()
            self.logger.info("Kafka producer closed")
