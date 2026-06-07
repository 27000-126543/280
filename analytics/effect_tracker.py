import json
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from config.settings import SYSTEM_CONFIG, REDIS_CONFIG
from common.models import PushChannel
from common.logger import LoggerManager
import redis


class ABTester:
    def __init__(self):
        self.logger = LoggerManager.get_logger("ab_testing")
        self.redis_client = redis.Redis(**REDIS_CONFIG)
        self.enabled = SYSTEM_CONFIG["enable_ab_testing"]
        self.test_ratio = SYSTEM_CONFIG["ab_test_ratio"]

    def assign_group(self, user_id: str, test_name: str = "recommendation_push") -> str:
        if not self.enabled:
            return "control"

        try:
            key = f"ab_group:{test_name}:{user_id}"
            existing_group = self.redis_client.get(key)
            if existing_group:
                return existing_group.decode() if isinstance(existing_group, bytes) else existing_group

            group = "treatment" if random.random() < self.test_ratio else "control"
            self.redis_client.setex(key, timedelta(days=30), group)

            test_key = f"ab_test:{test_name}:users"
            self.redis_client.sadd(test_key, user_id)

            LoggerManager.log_operation(
                "ab_testing",
                "assign_group",
                user_id=user_id,
                details=f"test={test_name}, group={group}",
            )
            return group
        except Exception as e:
            LoggerManager.log_error("ab_testing", "assign_group", e, user_id=user_id)
            return "control"

    def batch_assign_groups(self, user_ids: List[str], test_name: str = "recommendation_push") -> Dict[str, str]:
        return {user_id: self.assign_group(user_id, test_name) for user_id in user_ids}

    def get_group(self, user_id: str, test_name: str = "recommendation_push") -> Optional[str]:
        try:
            key = f"ab_group:{test_name}:{user_id}"
            group = self.redis_client.get(key)
            if group:
                return group.decode() if isinstance(group, bytes) else group
        except Exception as e:
            LoggerManager.log_error("ab_testing", "get_group", e, user_id=user_id)
        return None


class EffectTracker:
    def __init__(self):
        self.logger = LoggerManager.get_logger("effect_tracking")
        self.redis_client = redis.Redis(**REDIS_CONFIG)

    def record_impression(self, user_id: str, push_id: str, channel: PushChannel, ab_group: Optional[str] = None):
        try:
            date_key = datetime.now().strftime("%Y%m%d")
            pipeline = self.redis_client.pipeline()

            pipeline.incr(f"stats:impressions:{date_key}:{channel.value}")
            pipeline.incr(f"stats:impressions:{date_key}:total")

            if ab_group:
                pipeline.incr(f"ab_stats:impressions:{date_key}:{ab_group}")

            pipeline.execute()

            user_impressions_key = f"user_impressions:{user_id}"
            self.redis_client.sadd(user_impressions_key, push_id)
            self.redis_client.expire(user_impressions_key, timedelta(days=90))

        except Exception as e:
            LoggerManager.log_error("effect_tracking", "record_impression", e, user_id=user_id)

    def record_click(self, user_id: str, push_id: str, channel: PushChannel, item_id: str, ab_group: Optional[str] = None):
        try:
            date_key = datetime.now().strftime("%Y%m%d")
            pipeline = self.redis_client.pipeline()

            pipeline.incr(f"stats:clicks:{date_key}:{channel.value}")
            pipeline.incr(f"stats:clicks:{date_key}:total")
            pipeline.hincrby(f"stats:item_clicks:{date_key}", item_id, 1)

            if ab_group:
                pipeline.incr(f"ab_stats:clicks:{date_key}:{ab_group}")

            pipeline.execute()

            push_key = f"push_performance:{push_id}"
            self.redis_client.hincrby(push_key, "clicks", 1)
            self.redis_client.expire(push_key, timedelta(days=90))

        except Exception as e:
            LoggerManager.log_error("effect_tracking", "record_click", e, user_id=user_id)

    def record_conversion(
        self,
        user_id: str,
        push_id: str,
        channel: PushChannel,
        value: float,
        item_id: Optional[str] = None,
        ab_group: Optional[str] = None,
    ):
        try:
            date_key = datetime.now().strftime("%Y%m%d")
            pipeline = self.redis_client.pipeline()

            pipeline.incr(f"stats:conversions:{date_key}:{channel.value}")
            pipeline.incr(f"stats:conversions:{date_key}:total")
            pipeline.incrbyfloat(f"stats:revenue:{date_key}:{channel.value}", value)
            pipeline.incrbyfloat(f"stats:revenue:{date_key}:total", value)

            if ab_group:
                pipeline.incr(f"ab_stats:conversions:{date_key}:{ab_group}")
                pipeline.incrbyfloat(f"ab_stats:revenue:{date_key}:{ab_group}", value)

            pipeline.execute()

            push_key = f"push_performance:{push_id}"
            self.redis_client.hincrby(push_key, "conversions", 1)
            self.redis_client.hincrbyfloat(push_key, "revenue", value)
            self.redis_client.expire(push_key, timedelta(days=90))

        except Exception as e:
            LoggerManager.log_error("effect_tracking", "record_conversion", e, user_id=user_id)

    def get_daily_stats(self, date: Optional[datetime] = None, channel: Optional[PushChannel] = None) -> Dict:
        if date is None:
            date = datetime.now()

        date_key = date.strftime("%Y%m%d")
        stats = {}

        try:
            channel_key = channel.value if channel else "total"
            impressions = self.redis_client.get(f"stats:impressions:{date_key}:{channel_key}") or 0
            clicks = self.redis_client.get(f"stats:clicks:{date_key}:{channel_key}") or 0
            conversions = self.redis_client.get(f"stats:conversions:{date_key}:{channel_key}") or 0
            revenue = self.redis_client.get(f"stats:revenue:{date_key}:{channel_key}") or 0

            impressions = int(impressions) if impressions else 0
            clicks = int(clicks) if clicks else 0
            conversions = int(conversions) if conversions else 0
            revenue = float(revenue) if revenue else 0.0

            ctr = clicks / impressions if impressions > 0 else 0.0
            cvr = conversions / clicks if clicks > 0 else 0.0
            avg_order_value = revenue / conversions if conversions > 0 else 0.0

            stats = {
                "date": date_key,
                "channel": channel_key,
                "impressions": impressions,
                "clicks": clicks,
                "conversions": conversions,
                "revenue": revenue,
                "ctr": ctr,
                "cvr": cvr,
                "avg_order_value": avg_order_value,
            }
        except Exception as e:
            LoggerManager.log_error("effect_tracking", "get_daily_stats", e)

        return stats

    def get_ab_test_results(self, test_name: str = "recommendation_push", days: int = 7) -> Dict:
        results = {"treatment": {}, "control": {}}

        try:
            for group in ["treatment", "control"]:
                impressions = 0
                clicks = 0
                conversions = 0
                revenue = 0.0

                for i in range(days):
                    date = datetime.now() - timedelta(days=i)
                    date_key = date.strftime("%Y%m%d")

                    imp = self.redis_client.get(f"ab_stats:impressions:{date_key}:{group}") or 0
                    clk = self.redis_client.get(f"ab_stats:clicks:{date_key}:{group}") or 0
                    conv = self.redis_client.get(f"ab_stats:conversions:{date_key}:{group}") or 0
                    rev = self.redis_client.get(f"ab_stats:revenue:{date_key}:{group}") or 0

                    impressions += int(imp) if imp else 0
                    clicks += int(clk) if clk else 0
                    conversions += int(conv) if conv else 0
                    revenue += float(rev) if rev else 0.0

                ctr = clicks / impressions if impressions > 0 else 0.0
                cvr = conversions / clicks if clicks > 0 else 0.0

                results[group] = {
                    "impressions": impressions,
                    "clicks": clicks,
                    "conversions": conversions,
                    "revenue": revenue,
                    "ctr": ctr,
                    "cvr": cvr,
                }

            if results["treatment"]["impressions"] > 0 and results["control"]["impressions"] > 0:
                results["lift"] = {
                    "ctr": (results["treatment"]["ctr"] - results["control"]["ctr"])
                    / results["control"]["ctr"]
                    if results["control"]["ctr"] > 0
                    else 0,
                    "cvr": (results["treatment"]["cvr"] - results["control"]["cvr"])
                    / results["control"]["cvr"]
                    if results["control"]["cvr"] > 0
                    else 0,
                    "revenue": (results["treatment"]["revenue"] - results["control"]["revenue"])
                    / results["control"]["revenue"]
                    if results["control"]["revenue"] > 0
                    else 0,
                }

        except Exception as e:
            LoggerManager.log_error("effect_tracking", "get_ab_test_results", e)

        return results

    def get_trend_data(self, days: int = 30, channel: Optional[PushChannel] = None) -> List[Dict]:
        trend_data = []
        try:
            for i in range(days - 1, -1, -1):
                date = datetime.now() - timedelta(days=i)
                daily_stats = self.get_daily_stats(date, channel)
                if daily_stats:
                    trend_data.append(daily_stats)
        except Exception as e:
            LoggerManager.log_error("effect_tracking", "get_trend_data", e)
        return trend_data

    def get_channel_comparison(self, date: Optional[datetime] = None) -> Dict:
        if date is None:
            date = datetime.now()

        comparison = {}
        try:
            for channel in PushChannel:
                stats = self.get_daily_stats(date, channel)
                if stats:
                    comparison[channel.value] = stats
        except Exception as e:
            LoggerManager.log_error("effect_tracking", "get_channel_comparison", e)
        return comparison
