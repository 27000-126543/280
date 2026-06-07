import os
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASE_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "password",
    "database": "user_behavior_analysis",
    "charset": "utf8mb4",
    "pool_size": 100,
    "max_overflow": 50,
}

REDIS_CONFIG = {
    "host": "localhost",
    "port": 6379,
    "db": 0,
    "password": None,
    "max_connections": 1000,
    "decode_responses": True,
}

KAFKA_CONFIG = {
    "bootstrap_servers": ["localhost:9092"],
    "group_id": "behavior_consumer_group",
    "auto_offset_reset": "earliest",
    "enable_auto_commit": False,
    "topics": {
        "user_behavior": "user_behavior_events",
        "recommendation_results": "recommendation_results",
        "push_events": "push_events",
    },
}

CLICKHOUSE_CONFIG = {
    "host": "localhost",
    "port": 8123,
    "user": "default",
    "password": "",
    "database": "behavior_analytics",
    "pool_size": 50,
}

SYSTEM_CONFIG = {
    "timezone": "Asia/Shanghai",
    "max_concurrent_tasks": 500,
    "batch_size": 10000,
    "realtime_window_minutes": 30,
    "retention_days": 180,
    "enable_ab_testing": True,
    "ab_test_ratio": 0.1,
}

RECOMMENDATION_CONFIG = {
    "candidate_set_size": 500,
    "final_recommend_count": 10,
    "ctr_prediction_threshold": 0.01,
    "industry_benchmark_ctr": 0.03,
    "low_ctr_days_threshold": 3,
    "similar_scenario_count": 5,
    "cold_start_default_tags": ["新品推荐", "热门商品", "限时优惠"],
}

PUSH_CONFIG = {
    "channels": ["email", "in_app", "sms"],
    "default_push_time": "09:00",
    "email_config": {
        "smtp_server": "smtp.example.com",
        "smtp_port": 587,
        "sender_email": "recommend@example.com",
        "sender_password": "password",
    },
    "sms_config": {
        "api_key": "your_sms_api_key",
        "api_secret": "your_sms_api_secret",
        "sign_name": "【企业名称】",
    },
    "rate_limit": {
        "email": 10000,
        "sms": 5000,
        "in_app": 50000,
    },
    "cooldown_hours": 24,
}

REPORT_CONFIG = {
    "generate_time": "02:00",
    "output_dir": os.path.join(BASE_DIR, "data", "reports"),
    "formats": ["pdf", "excel"],
    "trend_days": 30,
    "enable_charts": True,
}

LOG_CONFIG = {
    "level": "INFO",
    "log_dir": os.path.join(BASE_DIR, "data", "logs"),
    "max_bytes": 100 * 1024 * 1024,
    "backup_count": 30,
    "modules": ["data_collection", "recommendation", "push", "tracking", "report"],
}
