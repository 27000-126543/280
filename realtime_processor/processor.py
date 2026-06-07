import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict
from kafka import KafkaConsumer
from concurrent.futures import ThreadPoolExecutor, as_completed
from config.settings import KAFKA_CONFIG, SYSTEM_CONFIG, REDIS_CONFIG
from common.models import UserProfile, UserBehaviorEvent, BehaviorType, UserLifecycle
from common.logger import LoggerManager
import redis


class RealtimeProcessor:
    def __init__(self):
        self.logger = LoggerManager.get_logger("realtime_processor")
        self.redis_client = redis.Redis(**REDIS_CONFIG)
        self.executor = ThreadPoolExecutor(max_workers=SYSTEM_CONFIG["max_concurrent_tasks"])
        self.behavior_weights = {
            BehaviorType.VIEW: 1.0,
            BehaviorType.CLICK: 2.0,
            BehaviorType.COLLECT: 4.0,
            BehaviorType.SHARE: 3.0,
            BehaviorType.ADD_TO_CART: 5.0,
            BehaviorType.PURCHASE: 10.0,
            BehaviorType.SEARCH: 1.5,
        }
        self.tag_decay_rate = 0.95
        self.intent_window_hours = 24

    def process_event(self, event_data: Dict):
        try:
            event = UserBehaviorEvent(
                event_id=event_data["event_id"],
                user_id=event_data["user_id"],
                behavior_type=BehaviorType(event_data["behavior_type"]),
                channel=event_data["channel"],
                item_id=event_data.get("item_id"),
                item_type=event_data.get("item_type"),
                category=event_data.get("category"),
                timestamp=datetime.fromisoformat(event_data["timestamp"]),
                session_id=event_data.get("session_id"),
                page_url=event_data.get("page_url"),
                referrer=event_data.get("referrer"),
                ip_address=event_data.get("ip_address"),
                user_agent=event_data.get("user_agent"),
                duration=event_data.get("duration"),
                price=event_data.get("price"),
                quantity=event_data.get("quantity"),
                extra=event_data.get("extra", {}),
            )

            user_id = event.user_id
            profile = self._get_user_profile(user_id)
            self._update_interest_tags(profile, event)
            self._update_lifecycle_stage(profile, event)
            self._update_recent_intent(profile, event)
            self._update_purchase_stats(profile, event)
            self._save_user_profile(profile)

            LoggerManager.log_operation(
                "realtime_processor",
                "process_event",
                user_id=user_id,
                details=f"lifecycle={profile.lifecycle_stage.value}, tags_count={len(profile.interest_tags)}",
            )
        except Exception as e:
            LoggerManager.log_error(
                "realtime_processor",
                "process_event",
                e,
                details=f"event_data={event_data}",
            )

    def _get_user_profile(self, user_id: str) -> UserProfile:
        try:
            key = f"user_profile:{user_id}"
            profile_data = self.redis_client.get(key)
            if profile_data:
                data = json.loads(profile_data)
                return UserProfile(
                    user_id=user_id,
                    interest_tags=data.get("interest_tags", {}),
                    lifecycle_stage=UserLifecycle(data.get("lifecycle_stage", "new")),
                    recent_intent=data.get("recent_intent"),
                    intent_confidence=data.get("intent_confidence", 0.0),
                    first_active_time=datetime.fromisoformat(data["first_active_time"])
                    if data.get("first_active_time")
                    else None,
                    last_active_time=datetime.fromisoformat(data["last_active_time"])
                    if data.get("last_active_time")
                    else None,
                    total_orders=data.get("total_orders", 0),
                    total_spent=data.get("total_spent", 0.0),
                    avg_order_value=data.get("avg_order_value", 0.0),
                    purchase_frequency=data.get("purchase_frequency", 0.0),
                    last_purchase_time=datetime.fromisoformat(data["last_purchase_time"])
                    if data.get("last_purchase_time")
                    else None,
                    browse_categories=data.get("browse_categories", []),
                    preferred_price_range=tuple(data["preferred_price_range"])
                    if data.get("preferred_price_range")
                    else None,
                    preferred_brands=data.get("preferred_brands", []),
                )
            return UserProfile(user_id=user_id)
        except Exception as e:
            LoggerManager.log_error(
                "realtime_processor", "get_user_profile", e, user_id=user_id
            )
            return UserProfile(user_id=user_id)

    def _save_user_profile(self, profile: UserProfile):
        try:
            key = f"user_profile:{profile.user_id}"
            data = {
                "user_id": profile.user_id,
                "interest_tags": profile.interest_tags,
                "lifecycle_stage": profile.lifecycle_stage.value,
                "recent_intent": profile.recent_intent,
                "intent_confidence": profile.intent_confidence,
                "first_active_time": profile.first_active_time.isoformat()
                if profile.first_active_time
                else None,
                "last_active_time": profile.last_active_time.isoformat()
                if profile.last_active_time
                else None,
                "total_orders": profile.total_orders,
                "total_spent": profile.total_spent,
                "avg_order_value": profile.avg_order_value,
                "purchase_frequency": profile.purchase_frequency,
                "last_purchase_time": profile.last_purchase_time.isoformat()
                if profile.last_purchase_time
                else None,
                "browse_categories": profile.browse_categories,
                "preferred_price_range": list(profile.preferred_price_range)
                if profile.preferred_price_range
                else None,
                "preferred_brands": profile.preferred_brands,
            }
            self.redis_client.setex(
                key, timedelta(days=SYSTEM_CONFIG["retention_days"]), json.dumps(data)
            )
        except Exception as e:
            LoggerManager.log_error(
                "realtime_processor", "save_user_profile", e, user_id=profile.user_id
            )

    def _update_interest_tags(self, profile: UserProfile, event: UserBehaviorEvent):
        weight = self.behavior_weights.get(event.behavior_type, 1.0)
        tags = []
        if event.category:
            tags.append(event.category)
        if event.item_type:
            tags.append(event.item_type)
        if event.extra and "tags" in event.extra:
            tags.extend(event.extra["tags"])

        for tag in tags:
            if tag:
                current_score = profile.interest_tags.get(tag, 0.0)
                profile.interest_tags[tag] = current_score * self.tag_decay_rate + weight

        sorted_tags = sorted(
            profile.interest_tags.items(), key=lambda x: x[1], reverse=True
        )
        profile.interest_tags = dict(sorted_tags[:100])

    def _update_lifecycle_stage(self, profile: UserProfile, event: UserBehaviorEvent):
        now = datetime.now()
        if not profile.first_active_time:
            profile.first_active_time = event.timestamp
        profile.last_active_time = event.timestamp

        days_since_first = (now - profile.first_active_time).days
        days_since_active = (now - profile.last_active_time).days if profile.last_active_time else 999
        days_since_purchase = (
            (now - profile.last_purchase_time).days if profile.last_purchase_time else 999
        )

        if days_since_first <= 7 and profile.total_orders == 0:
            profile.lifecycle_stage = UserLifecycle.NEW
        elif profile.total_orders > 0 and days_since_purchase <= 30:
            profile.lifecycle_stage = UserLifecycle.ACTIVE
        elif days_since_active <= 7 and profile.total_orders > 0:
            profile.lifecycle_stage = UserLifecycle.ACTIVE
        elif 7 < days_since_active <= 30:
            profile.lifecycle_stage = UserLifecycle.AT_RISK
        elif 30 < days_since_active <= 90:
            profile.lifecycle_stage = UserLifecycle.DORMANT
        else:
            profile.lifecycle_stage = UserLifecycle.CHURNED

    def _update_recent_intent(self, profile: UserProfile, event: UserBehaviorEvent):
        window_start = datetime.now() - timedelta(hours=self.intent_window_hours)
        if event.timestamp >= window_start:
            intent_signals = []
            if event.behavior_type in [
                BehaviorType.SEARCH,
                BehaviorType.VIEW,
                BehaviorType.CLICK,
            ]:
                if event.category:
                    intent_signals.append(("browse", event.category, 0.3))
            if event.behavior_type == BehaviorType.ADD_TO_CART:
                if event.category:
                    intent_signals.append(("purchase_intent", event.category, 0.7))
            if event.behavior_type == BehaviorType.COLLECT:
                if event.category:
                    intent_signals.append(("interest", event.category, 0.5))

            if intent_signals:
                best_signal = max(intent_signals, key=lambda x: x[2])
                profile.recent_intent = f"{best_signal[0]}_{best_signal[1]}"
                profile.intent_confidence = min(
                    profile.intent_confidence * 0.8 + best_signal[2], 1.0
                )

    def _update_purchase_stats(self, profile: UserProfile, event: UserBehaviorEvent):
        if event.behavior_type == BehaviorType.PURCHASE and event.price and event.quantity:
            total_price = event.price * event.quantity
            profile.total_orders += 1
            profile.total_spent += total_price
            profile.last_purchase_time = event.timestamp
            profile.avg_order_value = profile.total_spent / profile.total_orders

            if profile.first_active_time:
                days_active = max(
                    (event.timestamp - profile.first_active_time).days, 1
                )
                profile.purchase_frequency = profile.total_orders / days_active * 30

        if event.category and event.category not in profile.browse_categories:
            profile.browse_categories.append(event.category)
            if len(profile.browse_categories) > 20:
                profile.browse_categories = profile.browse_categories[-20:]

        if event.price and event.behavior_type in [
            BehaviorType.VIEW,
            BehaviorType.ADD_TO_CART,
            BehaviorType.PURCHASE,
        ]:
            prices = []
            if profile.preferred_price_range:
                prices = list(profile.preferred_price_range)
            prices.append(event.price)
            if len(prices) >= 3:
                avg = sum(prices) / len(prices)
                profile.preferred_price_range = (avg * 0.7, avg * 1.3)

    def process_stream(self):
        self.logger.info("Starting realtime event processing stream")
        consumer = KafkaConsumer(
            KAFKA_CONFIG["topics"]["user_behavior"],
            bootstrap_servers=KAFKA_CONFIG["bootstrap_servers"],
            group_id=KAFKA_CONFIG["group_id"],
            auto_offset_reset=KAFKA_CONFIG["auto_offset_reset"],
            enable_auto_commit=KAFKA_CONFIG["enable_auto_commit"],
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            max_poll_records=SYSTEM_CONFIG["batch_size"],
        )

        try:
            for message in consumer:
                futures = []
                batch = [message.value]
                batch.extend(
                    [
                        msg.value
                        for msg in consumer.poll(
                            timeout_ms=100, max_records=SYSTEM_CONFIG["batch_size"] - 1
                        ).values()
                    ][0]
                    if consumer.poll(timeout_ms=100, max_records=SYSTEM_CONFIG["batch_size"] - 1)
                    else []
                )

                for event_data in batch:
                    future = self.executor.submit(self.process_event, event_data)
                    futures.append(future)

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        LoggerManager.log_error(
                            "realtime_processor", "process_stream_task", e
                        )

                consumer.commit()
        except Exception as e:
            LoggerManager.log_error("realtime_processor", "process_stream", e)
        finally:
            consumer.close()
            self.executor.shutdown()

    def get_user_profile(self, user_id: str) -> Optional[UserProfile]:
        return self._get_user_profile(user_id)

    def batch_update_profiles(self, user_ids: List[str]):
        profiles = []
        for user_id in user_ids:
            profile = self._get_user_profile(user_id)
            profiles.append(profile)
        return profiles
