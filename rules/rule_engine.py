import json
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from config.settings import REDIS_CONFIG, SYSTEM_CONFIG
from common.models import Rule, UserProfile, RecommendItem
from common.logger import LoggerManager
import redis


class RuleEngine:
    def __init__(self):
        self.logger = LoggerManager.get_logger("rule_engine")
        self.redis_client = redis.Redis(**REDIS_CONFIG)
        self.rules_key = "recommendation_rules"

    def create_rule(
        self,
        name: str,
        description: str,
        conditions: Dict,
        actions: Dict,
        priority: int = 0,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        created_by: str = "system",
    ) -> Rule:
        rule_id = str(uuid.uuid4())
        rule = Rule(
            rule_id=rule_id,
            name=name,
            description=description,
            priority=priority,
            conditions=conditions,
            actions=actions,
            start_time=start_time,
            end_time=end_time,
            enabled=True,
            created_by=created_by,
            created_at=datetime.now(),
        )

        conflicts = self._check_conflicts(rule)
        if conflicts:
            self.logger.warning(
                f"Rule {name} has conflicts with existing rules: {[c['rule_id'] for c in conflicts]}"
            )
            rule = self._merge_rule_with_conflicts(rule, conflicts)

        self._save_rule(rule)
        LoggerManager.log_operation(
            "rule_engine",
            "create_rule",
            details=f"rule_id={rule_id}, name={name}, conflicts={len(conflicts)}",
        )
        return rule

    def _check_conflicts(self, new_rule: Rule) -> List[Dict]:
        conflicts = []
        try:
            existing_rules = self.get_all_rules(enabled_only=True)
            for existing_rule in existing_rules:
                if existing_rule.rule_id == new_rule.rule_id:
                    continue

                conflict_type = self._detect_conflict(new_rule, existing_rule)
                if conflict_type:
                    conflicts.append(
                        {
                            "rule_id": existing_rule.rule_id,
                            "rule_name": existing_rule.name,
                            "conflict_type": conflict_type,
                            "severity": "high" if conflict_type == "action_overlap" else "medium",
                        }
                    )
        except Exception as e:
            LoggerManager.log_error("rule_engine", "_check_conflicts", e)
        return conflicts

    def _detect_conflict(self, rule1: Rule, rule2: Rule) -> Optional[str]:
        if self._time_overlap(rule1, rule2):
            if self._condition_overlap(rule1.conditions, rule2.conditions):
                if self._action_overlap(rule1.actions, rule2.actions):
                    return "action_overlap"
                return "condition_overlap"
        return None

    def _time_overlap(self, rule1: Rule, rule2: Rule) -> bool:
        r1_start = rule1.start_time or datetime.min
        r1_end = rule1.end_time or datetime.max
        r2_start = rule2.start_time or datetime.min
        r2_end = rule2.end_time or datetime.max
        return r1_start < r2_end and r2_start < r1_end

    def _condition_overlap(self, cond1: Dict, cond2: Dict) -> bool:
        overlap = False
        for key in cond1:
            if key in cond2:
                val1 = cond1[key]
                val2 = cond2[key]
                if isinstance(val1, list) and isinstance(val2, list):
                    if set(val1) & set(val2):
                        overlap = True
                elif val1 == val2:
                    overlap = True
        return overlap

    def _action_overlap(self, action1: Dict, action2: Dict) -> bool:
        action_keys1 = set(action1.keys())
        action_keys2 = set(action2.keys())
        return bool(action_keys1 & action_keys2)

    def _merge_rule_with_conflicts(self, new_rule: Rule, conflicts: List[Dict]) -> Rule:
        if not conflicts:
            return new_rule

        existing_rules = {r.rule_id: r for r in self.get_all_rules()}
        max_priority = max(
            [existing_rules[c["rule_id"]].priority for c in conflicts if c["rule_id"] in existing_rules]
            + [new_rule.priority]
        )
        new_rule.priority = max_priority + 1

        return new_rule

    def _save_rule(self, rule: Rule):
        try:
            key = f"rule:{rule.rule_id}"
            rule_data = {
                "rule_id": rule.rule_id,
                "name": rule.name,
                "description": rule.description,
                "priority": rule.priority,
                "conditions": rule.conditions,
                "actions": rule.actions,
                "start_time": rule.start_time.isoformat() if rule.start_time else None,
                "end_time": rule.end_time.isoformat() if rule.end_time else None,
                "enabled": rule.enabled,
                "created_by": rule.created_by,
                "created_at": rule.created_at.isoformat(),
            }
            self.redis_client.set(key, json.dumps(rule_data))
            self.redis_client.sadd(self.rules_key, rule.rule_id)
        except Exception as e:
            LoggerManager.log_error("rule_engine", "_save_rule", e)

    def get_rule(self, rule_id: str) -> Optional[Rule]:
        try:
            key = f"rule:{rule_id}"
            data = self.redis_client.get(key)
            if data:
                rule_data = json.loads(data)
                return Rule(
                    rule_id=rule_data["rule_id"],
                    name=rule_data["name"],
                    description=rule_data["description"],
                    priority=rule_data["priority"],
                    conditions=rule_data["conditions"],
                    actions=rule_data["actions"],
                    start_time=datetime.fromisoformat(rule_data["start_time"])
                    if rule_data.get("start_time")
                    else None,
                    end_time=datetime.fromisoformat(rule_data["end_time"])
                    if rule_data.get("end_time")
                    else None,
                    enabled=rule_data["enabled"],
                    created_by=rule_data.get("created_by"),
                    created_at=datetime.fromisoformat(rule_data["created_at"])
                    if rule_data.get("created_at")
                    else datetime.now(),
                )
        except Exception as e:
            LoggerManager.log_error("rule_engine", "get_rule", e)
        return None

    def get_all_rules(self, enabled_only: bool = False) -> List[Rule]:
        rules = []
        try:
            rule_ids = self.redis_client.smembers(self.rules_key)
            for rule_id in rule_ids:
                rule_id_str = rule_id.decode() if isinstance(rule_id, bytes) else rule_id
                rule = self.get_rule(rule_id_str)
                if rule:
                    if enabled_only and not rule.enabled:
                        continue
                    if not self._is_rule_active(rule):
                        continue
                    rules.append(rule)
        except Exception as e:
            LoggerManager.log_error("rule_engine", "get_all_rules", e)
        rules.sort(key=lambda r: r.priority, reverse=True)
        return rules

    def _is_rule_active(self, rule: Rule) -> bool:
        now = datetime.now()
        if rule.start_time and now < rule.start_time:
            return False
        if rule.end_time and now > rule.end_time:
            return False
        return rule.enabled

    def update_rule(self, rule_id: str, **kwargs) -> Optional[Rule]:
        rule = self.get_rule(rule_id)
        if not rule:
            return None

        for key, value in kwargs.items():
            if hasattr(rule, key):
                setattr(rule, key, value)

        conflicts = self._check_conflicts(rule)
        if conflicts:
            rule = self._merge_rule_with_conflicts(rule, conflicts)

        self._save_rule(rule)
        LoggerManager.log_operation(
            "rule_engine", "update_rule", details=f"rule_id={rule_id}, updated_fields={list(kwargs.keys())}"
        )
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        try:
            key = f"rule:{rule_id}"
            self.redis_client.delete(key)
            self.redis_client.srem(self.rules_key, rule_id)
            LoggerManager.log_operation("rule_engine", "delete_rule", details=f"rule_id={rule_id}")
            return True
        except Exception as e:
            LoggerManager.log_error("rule_engine", "delete_rule", e)
            return False

    def apply_rules(
        self, user_profile: UserProfile, recommendations: List[RecommendItem]
    ) -> List[RecommendItem]:
        try:
            active_rules = self.get_all_rules(enabled_only=True)
            modified_recommendations = recommendations.copy()

            for rule in active_rules:
                if self._matches_conditions(user_profile, rule.conditions):
                    modified_recommendations = self._apply_rule_actions(
                        rule, modified_recommendations, user_profile
                    )

            return modified_recommendations
        except Exception as e:
            LoggerManager.log_error("rule_engine", "apply_rules", e)
            return recommendations

    def _matches_conditions(self, user_profile: UserProfile, conditions: Dict) -> bool:
        try:
            if "lifecycle_stage" in conditions:
                target_stages = conditions["lifecycle_stage"]
                if isinstance(target_stages, str):
                    target_stages = [target_stages]
                if user_profile.lifecycle_stage.value not in target_stages:
                    return False

            if "user_tags" in conditions:
                required_tags = conditions["user_tags"]
                if isinstance(required_tags, str):
                    required_tags = [required_tags]
                if not any(tag in user_profile.interest_tags for tag in required_tags):
                    return False

            if "min_total_spent" in conditions:
                if user_profile.total_spent < conditions["min_total_spent"]:
                    return False

            if "max_total_spent" in conditions:
                if user_profile.total_spent > conditions["max_total_spent"]:
                    return False

            if "min_orders" in conditions:
                if user_profile.total_orders < conditions["min_orders"]:
                    return False

            if "recent_intent" in conditions:
                if user_profile.recent_intent != conditions["recent_intent"]:
                    return False

            if "date_range" in conditions:
                now = datetime.now()
                start_date = datetime.fromisoformat(conditions["date_range"]["start"])
                end_date = datetime.fromisoformat(conditions["date_range"]["end"])
                if not (start_date <= now <= end_date):
                    return False

            return True
        except Exception as e:
            LoggerManager.log_error("rule_engine", "_matches_conditions", e)
            return False

    def _apply_rule_actions(
        self, rule: Rule, recommendations: List[RecommendItem], user_profile: UserProfile
    ) -> List[RecommendItem]:
        try:
            actions = rule.actions

            if "boost_tags" in actions:
                boost_tags = actions["boost_tags"]
                boost_weight = actions.get("boost_weight", 2.0)
                for item in recommendations:
                    if any(tag in item.tags for tag in boost_tags):
                        item.score *= boost_weight

            if "demote_tags" in actions:
                demote_tags = actions["demote_tags"]
                demote_weight = actions.get("demote_weight", 0.5)
                for item in recommendations:
                    if any(tag in item.tags for tag in demote_tags):
                        item.score *= demote_weight

            if "force_include_tags" in actions:
                include_tags = actions["force_include_tags"]
                force_items = []
                from recommendation.engine import RecommendationEngine
                engine = RecommendationEngine()
                for items in engine.item_pool.values():
                    for item in items:
                        if any(tag in item.tags for tag in include_tags):
                            item_copy = RecommendItem(
                                item_id=item.item_id,
                                item_type=item.item_type,
                                title=item.title,
                                image_url=item.image_url,
                                target_url=item.target_url,
                                price=item.price,
                                original_price=item.original_price,
                                tags=item.tags.copy(),
                                category=item.category,
                                brand=item.brand,
                                score=999.0,
                                reason=f"规则推荐: {rule.name}",
                            )
                            force_items.append(item_copy)
                            if len(force_items) >= actions.get("max_include", 3):
                                break
                    if len(force_items) >= actions.get("max_include", 3):
                        break
                recommendations = force_items + recommendations

            if "exclude_tags" in actions:
                exclude_tags = actions["exclude_tags"]
                recommendations = [
                    item
                    for item in recommendations
                    if not any(tag in item.tags for tag in exclude_tags)
                ]

            if "custom_message" in actions:
                for item in recommendations[:3]:
                    item.reason = actions["custom_message"]

            recommendations.sort(key=lambda x: x.score, reverse=True)
            return recommendations

        except Exception as e:
            LoggerManager.log_error("rule_engine", "_apply_rule_actions", e)
            return recommendations

    def create_holiday_rule(
        self,
        holiday_name: str,
        start_date: datetime,
        end_date: datetime,
        promotional_tags: List[str],
        priority: int = 100,
    ) -> Rule:
        conditions = {
            "date_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            }
        }
        actions = {
            "boost_tags": promotional_tags,
            "boost_weight": 3.0,
            "force_include_tags": promotional_tags,
            "max_include": 5,
            "custom_message": f"{holiday_name}特别推荐",
        }
        return self.create_rule(
            name=f"{holiday_name}促销策略",
            description=f"{holiday_name}期间自动提升促销商品权重",
            conditions=conditions,
            actions=actions,
            priority=priority,
            start_time=start_date,
            end_time=end_date,
            created_by="system",
        )
