import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from config.settings import RECOMMENDATION_CONFIG, REDIS_CONFIG
from common.logger import LoggerManager
import redis


class ModelMonitor:
    def __init__(self):
        self.logger = LoggerManager.get_logger("model_monitor")
        self.redis_client = redis.Redis(**REDIS_CONFIG)
        self.benchmark_ctr = RECOMMENDATION_CONFIG["industry_benchmark_ctr"]
        self.low_ctr_days = RECOMMENDATION_CONFIG["low_ctr_days_threshold"]
        self.similar_scenario_count = RECOMMENDATION_CONFIG["similar_scenario_count"]

    def check_model_performance(self) -> Dict:
        try:
            ctr_history = self._get_ctr_history(self.low_ctr_days + 7)
            recent_ctr = ctr_history[-self.low_ctr_days :] if len(ctr_history) >= self.low_ctr_days else ctr_history

            below_benchmark_days = sum(
                1 for ctr in recent_ctr if ctr < self.benchmark_ctr
            )

            result = {
                "check_time": datetime.now().isoformat(),
                "benchmark_ctr": self.benchmark_ctr,
                "recent_ctr": recent_ctr,
                "below_benchmark_days": below_benchmark_days,
                "needs_adjustment": below_benchmark_days >= self.low_ctr_days,
            }

            if result["needs_adjustment"]:
                adjustment_suggestions = self._generate_adjustment_suggestions(ctr_history)
                result["adjustment_suggestions"] = adjustment_suggestions
                self._trigger_adjustment_alert(adjustment_suggestions)

            LoggerManager.log_operation(
                "model_monitor",
                "check_model_performance",
                details=f"below_days={below_benchmark_days}, needs_adjustment={result['needs_adjustment']}",
            )
            return result
        except Exception as e:
            LoggerManager.log_error("model_monitor", "check_model_performance", e)
            return {"error": str(e)}

    def _get_ctr_history(self, days: int) -> List[float]:
        ctr_history = []
        try:
            for i in range(days - 1, -1, -1):
                date = datetime.now() - timedelta(days=i)
                date_key = date.strftime("%Y%m%d")
                impressions = self.redis_client.get(f"stats:impressions:{date_key}:total") or 0
                clicks = self.redis_client.get(f"stats:clicks:{date_key}:total") or 0

                impressions = int(impressions) if impressions else 0
                clicks = int(clicks) if clicks else 0
                ctr = clicks / impressions if impressions > 0 else 0.0
                ctr_history.append(ctr)
        except Exception as e:
            LoggerManager.log_error("model_monitor", "_get_ctr_history", e)
        return ctr_history

    def _generate_adjustment_suggestions(self, ctr_history: List[float]) -> List[Dict]:
        suggestions = []
        try:
            similar_scenarios = self._find_similar_scenarios(ctr_history)

            if similar_scenarios:
                for scenario in similar_scenarios:
                    suggestion = {
                        "type": "historical_solution",
                        "scenario_date": scenario["date"],
                        "scenario_ctr": scenario["ctr"],
                        "applied_solution": scenario["solution"],
                        "result_improvement": scenario["improvement"],
                        "confidence": scenario["confidence"],
                    }
                    suggestions.append(suggestion)

            suggestions.extend(
                [
                    {
                        "type": "strategy_adjustment",
                        "suggestion": "提高热门商品和限时优惠的权重",
                        "expected_impact": "预计可提升CTR 15%-20%",
                        "action": "调整推荐引擎中的热门商品权重，从0.6提升至0.8",
                    },
                    {
                        "type": "strategy_adjustment",
                        "suggestion": "增加新品推荐的多样性",
                        "expected_impact": "预计可提升CTR 10%-15%",
                        "action": "增加候选集中新品的比例，从当前的10%提升至20%",
                    },
                    {
                        "type": "timing_adjustment",
                        "suggestion": "优化推送时间策略",
                        "expected_impact": "预计可提升CTR 5%-10%",
                        "action": "基于用户活跃时间分析，将用户分为早中晚三个时段组进行推送",
                    },
                ]
            )
        except Exception as e:
            LoggerManager.log_error("model_monitor", "_generate_adjustment_suggestions", e)
        return suggestions

    def _find_similar_scenarios(self, current_ctr_history: List[float]) -> List[Dict]:
        similar_scenarios = []
        try:
            historical_data_key = "historical_performance_scenarios"
            historical_data = self.redis_client.get(historical_data_key)
            if historical_data:
                scenarios = json.loads(historical_data)
                current_avg = sum(current_ctr_history[-7:]) / 7 if len(current_ctr_history) >= 7 else 0

                for scenario in scenarios:
                    scenario_avg = scenario.get("avg_ctr_before", 0)
                    similarity = 1 - abs(current_avg - scenario_avg) / max(current_avg, scenario_avg, 0.001)
                    if similarity > 0.7:
                        scenario["confidence"] = similarity
                        similar_scenarios.append(scenario)

                similar_scenarios.sort(key=lambda x: x["confidence"], reverse=True)
                similar_scenarios = similar_scenarios[: self.similar_scenario_count]
        except Exception as e:
            LoggerManager.log_error("model_monitor", "_find_similar_scenarios", e)
        return similar_scenarios

    def _trigger_adjustment_alert(self, suggestions: List[Dict]):
        try:
            alert = {
                "alert_id": f"alert_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "type": "model_performance_alert",
                "severity": "high",
                "message": f"推荐CTR已连续{self.low_ctr_days}天低于行业基准{self.benchmark_ctr*100:.1f}%",
                "suggestions": suggestions,
                "created_at": datetime.now().isoformat(),
                "status": "pending",
            }

            alerts_key = "model_alerts"
            self.redis_client.lpush(alerts_key, json.dumps(alert))
            self.redis_client.ltrim(alerts_key, 0, 99)

            self.logger.warning(
                f"MODEL ALERT: {alert['message']}. {len(suggestions)} suggestions generated."
            )
        except Exception as e:
            LoggerManager.log_error("model_monitor", "_trigger_adjustment_alert", e)

    def record_solution_result(self, date: str, solution: str, ctr_before: float, ctr_after: float):
        try:
            improvement = (ctr_after - ctr_before) / ctr_before if ctr_before > 0 else 0
            scenario = {
                "date": date,
                "solution": solution,
                "avg_ctr_before": ctr_before,
                "avg_ctr_after": ctr_after,
                "improvement": improvement,
            }

            historical_data_key = "historical_performance_scenarios"
            existing_data = self.redis_client.get(historical_data_key)
            scenarios = json.loads(existing_data) if existing_data else []
            scenarios.append(scenario)

            if len(scenarios) > 100:
                scenarios = scenarios[-100:]

            self.redis_client.set(historical_data_key, json.dumps(scenarios))
            LoggerManager.log_operation(
                "model_monitor",
                "record_solution_result",
                details=f"date={date}, improvement={improvement:.2%}",
            )
        except Exception as e:
            LoggerManager.log_error("model_monitor", "record_solution_result", e)

    def get_pending_alerts(self) -> List[Dict]:
        alerts = []
        try:
            alerts_key = "model_alerts"
            alerts_data = self.redis_client.lrange(alerts_key, 0, -1)
            for alert_str in alerts_data:
                alert = json.loads(alert_str)
                if alert.get("status") == "pending":
                    alerts.append(alert)
        except Exception as e:
            LoggerManager.log_error("model_monitor", "get_pending_alerts", e)
        return alerts

    def resolve_alert(self, alert_id: str, resolution: str, resolver: str):
        try:
            alerts_key = "model_alerts"
            alerts_data = self.redis_client.lrange(alerts_key, 0, -1)
            updated_alerts = []

            for alert_str in alerts_data:
                alert = json.loads(alert_str)
                if alert["alert_id"] == alert_id:
                    alert["status"] = "resolved"
                    alert["resolution"] = resolution
                    alert["resolved_by"] = resolver
                    alert["resolved_at"] = datetime.now().isoformat()
                updated_alerts.append(json.dumps(alert))

            if updated_alerts:
                self.redis_client.delete(alerts_key)
                self.redis_client.rpush(alerts_key, *updated_alerts)

            LoggerManager.log_operation(
                "model_monitor",
                "resolve_alert",
                details=f"alert_id={alert_id}, resolver={resolver}",
            )
        except Exception as e:
            LoggerManager.log_error("model_monitor", "resolve_alert", e)
