import time
import threading
import queue
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from collections import defaultdict
import schedule
from config.settings import SYSTEM_CONFIG, PUSH_CONFIG, REPORT_CONFIG, REDIS_CONFIG
from common.models import (
    UserProfile,
    RecommendItem,
    PushChannel,
    RecommendType,
    ChannelType,
    BehaviorType,
)
from common.logger import LoggerManager
from data_collection.collector import DataCollector
from realtime_processor.processor import RealtimeProcessor
from recommendation.engine import RecommendationEngine
from push.push_service import PushService
from analytics.effect_tracker import EffectTracker, ABTester
from analytics.model_monitor import ModelMonitor
from rules.rule_engine import RuleEngine
from report.generator import ReportGenerator
from query.query_service import QueryService
import redis


class Scheduler:
    def __init__(self):
        self.logger = LoggerManager.get_logger("scheduler")
        self.redis_client = redis.Redis(**REDIS_CONFIG)
        self.running = False
        self.task_queue = queue.Queue(maxsize=10000)
        self.executor = ThreadPoolExecutor(max_workers=SYSTEM_CONFIG["max_concurrent_tasks"])

        self.data_collector = DataCollector()
        self.realtime_processor = RealtimeProcessor()
        self.recommendation_engine = RecommendationEngine()
        self.push_service = PushService()
        self.effect_tracker = EffectTracker()
        self.ab_tester = ABTester()
        self.model_monitor = ModelMonitor()
        self.rule_engine = RuleEngine()
        self.report_generator = ReportGenerator()
        self.query_service = QueryService()

        self._init_sample_data()

    def _init_sample_data(self):
        try:
            sample_items = []
            categories = ["电子产品", "服装", "食品", "家居", "美妆", "图书", "运动"]
            brands = ["品牌A", "品牌B", "品牌C", "品牌D", "品牌E"]
            tags_pool = [
                "新品", "热门", "爆款", "限时优惠", "折扣", "新人专享",
                "会员专享", "精选推荐", "热销榜单", "复购推荐",
            ]

            for i in range(100):
                category = categories[i % len(categories)]
                brand = brands[i % len(brands)]
                item_type = RecommendType.PRODUCT if i % 3 != 2 else (
                    RecommendType.CONTENT if i % 3 == 1 else RecommendType.ACTIVITY
                )
                item = RecommendItem(
                    item_id=f"ITEM_{i:06d}",
                    item_type=item_type,
                    title=f"{category}商品{i} - {brand}",
                    price=9.9 + (i * 10) % 990,
                    original_price=19.9 + (i * 15) % 1490,
                    tags=[
                        tags_pool[i % len(tags_pool)],
                        tags_pool[(i + 3) % len(tags_pool)],
                        category,
                    ],
                    category=category,
                    brand=brand,
                    image_url=f"https://example.com/images/item_{i}.jpg",
                    target_url=f"https://example.com/products/{i}",
                )
                sample_items.append(item)
                self.recommendation_engine.add_item_to_pool(item)

            self.logger.info(f"Initialized {len(sample_items)} sample items")
        except Exception as e:
            LoggerManager.log_error("scheduler", "_init_sample_data", e)

    def start(self):
        self.logger.info("Starting scheduler...")
        self.running = True

        processing_thread = threading.Thread(target=self._process_tasks, daemon=True)
        processing_thread.start()

        schedule.every().day.at(PUSH_CONFIG["default_push_time"]).do(self._daily_push_task)
        schedule.every().day.at(REPORT_CONFIG["generate_time"]).do(self._daily_report_task)
        schedule.every(6).hours.do(self._model_check_task)
        schedule.every(1).hours.do(self._hourly_stats_task)
        schedule.every(10).minutes.do(self._realtime_processing_task)

        self.logger.info("Scheduler started successfully")

        try:
            while self.running:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self.logger.info("Stopping scheduler...")
        self.running = False
        self.data_collector.close()
        self.executor.shutdown(wait=True)
        self.logger.info("Scheduler stopped")

    def _process_tasks(self):
        while self.running:
            try:
                task = self.task_queue.get(timeout=1)
                if task:
                    self.executor.submit(self._execute_task, task)
            except queue.Empty:
                continue
            except Exception as e:
                LoggerManager.log_error("scheduler", "_process_tasks", e)

    def _execute_task(self, task: Dict):
        try:
            task_type = task.get("type")
            task_params = task.get("params", {})

            if task_type == "generate_recommendations":
                self._execute_generate_recommendations(task_params)
            elif task_type == "push_to_users":
                self._execute_push_to_users(task_params)
            elif task_type == "process_behavior_events":
                self._execute_process_behavior_events(task_params)
            elif task_type == "generate_report":
                self._execute_generate_report(task_params)
            else:
                self.logger.warning(f"Unknown task type: {task_type}")

            self.logger.info(f"Task executed: {task_type}")
        except Exception as e:
            LoggerManager.log_error("scheduler", "_execute_task", e, details=str(task))

    def _execute_generate_recommendations(self, params: Dict):
        try:
            user_ids = params.get("user_ids", [])
            if not user_ids:
                user_ids = self._get_active_user_ids()

            self.logger.info(f"Generating recommendations for {len(user_ids)} users")

            batch_size = SYSTEM_CONFIG["batch_size"]
            for i in range(0, len(user_ids), batch_size):
                batch_user_ids = user_ids[i : i + batch_size]
                user_profiles = {}

                for user_id in batch_user_ids:
                    profile = self.realtime_processor.get_user_profile(user_id)
                    if profile:
                        user_profiles[user_id] = profile

                recommendations = self.recommendation_engine.batch_generate(user_profiles)

                for user_id, recs in recommendations.items():
                    profile = user_profiles.get(user_id)
                    if profile:
                        recs = self.rule_engine.apply_rules(profile, recs)

            self.logger.info(f"Recommendations generated for {len(user_ids)} users")
        except Exception as e:
            LoggerManager.log_error("scheduler", "_execute_generate_recommendations", e)

    def _execute_push_to_users(self, params: Dict):
        try:
            channel = params.get("channel", PushChannel.IN_APP)
            user_ids = params.get("user_ids", [])

            if not user_ids:
                user_ids = self._get_active_user_ids()

            self.logger.info(f"Pushing to {len(user_ids)} users via {channel.value}")

            ab_groups = self.ab_tester.batch_assign_groups(user_ids)
            treatment_users = [uid for uid, group in ab_groups.items() if group == "treatment"]
            control_users = [uid for uid, group in ab_groups.items() if group == "control"]

            user_recommendations = {}
            for user_id in treatment_users:
                cached = self.recommendation_engine.get_cached_recommendations(user_id)
                if cached:
                    user_recommendations[user_id] = cached
                else:
                    profile = self.realtime_processor.get_user_profile(user_id)
                    if profile:
                        recs = self.recommendation_engine.generate_recommendations(user_id, profile)
                        recs = self.rule_engine.apply_rules(profile, recs)
                        user_recommendations[user_id] = recs

            push_records = self.push_service.batch_push(
                user_recommendations=user_recommendations,
                channel=channel,
                ab_test_groups={uid: "treatment" for uid in user_recommendations.keys()},
            )

            for record in push_records:
                if record.status == "sent":
                    self.effect_tracker.record_impression(
                        user_id=record.user_id,
                        push_id=record.push_id,
                        channel=channel,
                        ab_group=record.ab_test_group,
                    )

            self.logger.info(
                f"Push completed: {len(push_records)} records, treatment={len(treatment_users)}, control={len(control_users)}"
            )
        except Exception as e:
            LoggerManager.log_error("scheduler", "_execute_push_to_users", e)

    def _execute_process_behavior_events(self, params: Dict):
        try:
            events = params.get("events", [])
            if events:
                for event in events:
                    self.realtime_processor.process_event(event)

            self.logger.info(f"Processed {len(events)} behavior events")
        except Exception as e:
            LoggerManager.log_error("scheduler", "_execute_process_behavior_events", e)

    def _execute_generate_report(self, params: Dict):
        try:
            report_date = params.get("report_date")
            if report_date:
                if isinstance(report_date, str):
                    report_date = datetime.fromisoformat(report_date)
            else:
                report_date = datetime.now() - timedelta(days=1)

            report = self.report_generator.generate_daily_report(report_date)
            self.logger.info(f"Report generated: {report}")
        except Exception as e:
            LoggerManager.log_error("scheduler", "_execute_generate_report", e)

    def _daily_push_task(self):
        self.logger.info("Running daily push task")
        for channel_str in PUSH_CONFIG["channels"]:
            try:
                channel = PushChannel(channel_str)
                task = {
                    "type": "push_to_users",
                    "params": {"channel": channel},
                }
                self.task_queue.put(task)
            except Exception as e:
                LoggerManager.log_error("scheduler", "_daily_push_task", e, details=f"channel={channel_str}")

    def _daily_report_task(self):
        self.logger.info("Running daily report task")
        task = {"type": "generate_report", "params": {}}
        self.task_queue.put(task)

    def _model_check_task(self):
        self.logger.info("Running model performance check")
        try:
            result = self.model_monitor.check_model_performance()
            if result.get("needs_adjustment"):
                self.logger.warning(
                    f"Model performance below benchmark for {result.get('below_benchmark_days')} days"
                )
        except Exception as e:
            LoggerManager.log_error("scheduler", "_model_check_task", e)

    def _hourly_stats_task(self):
        self.logger.info("Running hourly stats aggregation")
        try:
            self._reset_daily_counters_if_needed()
        except Exception as e:
            LoggerManager.log_error("scheduler", "_hourly_stats_task", e)

    def _realtime_processing_task(self):
        pass

    def _get_active_user_ids(self, limit: int = 10000) -> List[str]:
        try:
            active_users = self.redis_client.smembers("active_users_today")
            user_ids = [uid.decode() if isinstance(uid, bytes) else uid for uid in active_users]
            return user_ids[:limit] if len(user_ids) > limit else user_ids
        except Exception as e:
            LoggerManager.log_error("scheduler", "_get_active_user_ids", e)
            return []

    def _reset_daily_counters_if_needed(self):
        today = datetime.now().strftime("%Y%m%d")
        last_reset = self.redis_client.get("last_daily_reset")
        if not last_reset or last_reset.decode() != today:
            self.redis_client.delete("active_users_today")
            self.redis_client.delete("new_users_today")
            self.redis_client.set("last_daily_reset", today)

    def submit_task(self, task_type: str, params: Dict = None) -> bool:
        try:
            task = {"type": task_type, "params": params or {}}
            self.task_queue.put(task, block=False)
            return True
        except queue.Full:
            self.logger.warning("Task queue is full, task rejected")
            return False

    def run_demo(self):
        self.logger.info("=" * 60)
        self.logger.info("Running system demo...")
        self.logger.info("=" * 60)

        try:
            user_ids = [f"USER_{i:06d}" for i in range(100)]

            self.logger.info("\n1. Simulating user behavior data collection...")
            channels = [ChannelType.WEBSITE, ChannelType.APP, ChannelType.MINIPROGRAM]
            behavior_types = [
                BehaviorType.VIEW,
                BehaviorType.CLICK,
                BehaviorType.ADD_TO_CART,
                BehaviorType.PURCHASE,
                BehaviorType.SEARCH,
                BehaviorType.COLLECT,
            ]
            categories = ["电子产品", "服装", "食品", "家居", "美妆", "图书", "运动"]

            import random

            for i in range(500):
                user_id = random.choice(user_ids)
                channel = random.choice(channels)
                behavior_type = random.choice(behavior_types)
                category = random.choice(categories)

                self.data_collector.collect_event(
                    user_id=user_id,
                    behavior_type=behavior_type,
                    channel=channel,
                    item_id=f"ITEM_{random.randint(0, 99):06d}",
                    category=category,
                    price=random.uniform(10, 1000) if behavior_type in [BehaviorType.PURCHASE, BehaviorType.ADD_TO_CART] else None,
                    quantity=random.randint(1, 3) if behavior_type == BehaviorType.PURCHASE else None,
                    extra={"tags": [category, "热门"]},
                )

            self.logger.info("   - Collected 500 user behavior events")

            self.logger.info("\n2. Processing behavior events in real-time...")
            for user_id in user_ids[:20]:
                profile = self.realtime_processor.get_user_profile(user_id)
                if profile:
                    self.logger.info(
                        f"   - User {user_id}: lifecycle={profile.lifecycle_stage.value}, "
                        f"tags={len(profile.interest_tags)}, intent={profile.recent_intent}"
                    )

            self.logger.info("\n3. Generating personalized recommendations...")
            test_user = user_ids[0]
            profile = self.realtime_processor.get_user_profile(test_user)
            if profile:
                recommendations = self.recommendation_engine.generate_recommendations(test_user, profile)
                recommendations = self.rule_engine.apply_rules(profile, recommendations)
                self.logger.info(f"   - Recommendations for user {test_user}:")
                for idx, rec in enumerate(recommendations[:5]):
                    self.logger.info(
                        f"     {idx + 1}. [{rec.item_type.value}] {rec.title} "
                        f"- score: {rec.score:.3f}, CTR: {rec.predicted_ctr:.2%} - {rec.reason}"
                    )

            self.logger.info("\n4. Creating a holiday promotion rule...")
            from datetime import datetime, timedelta

            holiday_rule = self.rule_engine.create_holiday_rule(
                holiday_name="618大促",
                start_date=datetime.now(),
                end_date=datetime.now() + timedelta(days=7),
                promotional_tags=["限时优惠", "折扣", "爆款"],
                priority=200,
            )
            self.logger.info(f"   - Created rule: {holiday_rule.name} (ID: {holiday_rule.rule_id})")

            self.logger.info("\n5. Running model performance check...")
            model_status = self.model_monitor.check_model_performance()
            self.logger.info(f"   - Below benchmark days: {model_status.get('below_benchmark_days', 0)}")
            self.logger.info(f"   - Needs adjustment: {model_status.get('needs_adjustment', False)}")

            self.logger.info("\n6. Generating daily report...")
            report = self.report_generator.generate_daily_report(datetime.now())
            if "error" not in report:
                self.logger.info(f"   - Excel report: {report.get('excel_path')}")
                self.logger.info(f"   - PDF report: {report.get('pdf_path')}")

            self.logger.info("\n7. Querying statistics summary...")
            stats = self.query_service.query_statistics_summary()
            self.logger.info(
                f"   - Total impressions: {stats['total_impressions']}, "
                f"clicks: {stats['total_clicks']}, "
                f"revenue: ¥{stats['total_revenue']:.2f}"
            )

            self.logger.info("\n" + "=" * 60)
            self.logger.info("Demo completed successfully!")
            self.logger.info("=" * 60)

        except Exception as e:
            LoggerManager.log_error("scheduler", "run_demo", e)
            self.logger.error(f"Demo failed: {e}")


def main():
    scheduler = Scheduler()
    try:
        scheduler.run_demo()
    except KeyboardInterrupt:
        scheduler.stop()


if __name__ == "__main__":
    main()
