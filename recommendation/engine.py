import json
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from collections import defaultdict
import numpy as np
from config.settings import RECOMMENDATION_CONFIG, REDIS_CONFIG
from common.models import (
    UserProfile,
    RecommendItem,
    RecommendType,
    UserLifecycle,
)
from common.logger import LoggerManager
import redis


class RecommendationEngine:
    def __init__(self):
        self.logger = LoggerManager.get_logger("recommendation")
        self.redis_client = redis.Redis(**REDIS_CONFIG)
        self.candidate_size = RECOMMENDATION_CONFIG["candidate_set_size"]
        self.final_count = RECOMMENDATION_CONFIG["final_recommend_count"]
        self.ctr_threshold = RECOMMENDATION_CONFIG["ctr_prediction_threshold"]
        self.item_pool = self._load_item_pool()

    def _load_item_pool(self) -> Dict[str, List[RecommendItem]]:
        item_pool = defaultdict(list)
        try:
            for item_type in RecommendType:
                key = f"item_pool:{item_type.value}"
                items_data = self.redis_client.get(key)
                if items_data:
                    items_list = json.loads(items_data)
                    for item_data in items_list:
                        item = RecommendItem(
                            item_id=item_data["item_id"],
                            item_type=RecommendType(item_data["item_type"]),
                            title=item_data["title"],
                            image_url=item_data.get("image_url"),
                            target_url=item_data.get("target_url"),
                            price=item_data.get("price"),
                            original_price=item_data.get("original_price"),
                            tags=item_data.get("tags", []),
                            category=item_data.get("category"),
                            brand=item_data.get("brand"),
                        )
                        item_pool[item_type.value].append(item)
        except Exception as e:
            LoggerManager.log_error("recommendation", "load_item_pool", e)
        return dict(item_pool)

    def generate_recommendations(
        self, user_id: str, profile: UserProfile
    ) -> List[RecommendItem]:
        try:
            candidates = self._generate_candidates(profile)
            scored_candidates = self._score_and_rank(candidates, profile)
            final_recommendations = self._diversify_and_select(scored_candidates)
            self._cache_recommendations(user_id, final_recommendations)

            LoggerManager.log_operation(
                "recommendation",
                "generate_recommendations",
                user_id=user_id,
                details=f"count={len(final_recommendations)}",
            )
            return final_recommendations
        except Exception as e:
            LoggerManager.log_error(
                "recommendation", "generate_recommendations", e, user_id=user_id
            )
            return self._get_cold_start_recommendations()

    def _generate_candidates(self, profile: UserProfile) -> List[RecommendItem]:
        candidates = []
        seen_items = set()

        if profile.interest_tags:
            tag_based = self._get_tag_based_candidates(profile.interest_tags)
            for item in tag_based:
                if item.item_id not in seen_items:
                    candidates.append(item)
                    seen_items.add(item.item_id)

        if profile.recent_intent:
            intent_based = self._get_intent_based_candidates(profile.recent_intent)
            for item in intent_based:
                if item.item_id not in seen_items:
                    candidates.append(item)
                    seen_items.add(item.item_id)

        lifecycle_based = self._get_lifecycle_based_candidates(profile.lifecycle_stage)
        for item in lifecycle_based:
            if item.item_id not in seen_items:
                candidates.append(item)
                seen_items.add(item.item_id)

        popular = self._get_popular_candidates()
        for item in popular:
            if item.item_id not in seen_items:
                candidates.append(item)
                seen_items.add(item.item_id)

        new_items = self._get_new_items_candidates()
        for item in new_items:
            if item.item_id not in seen_items:
                candidates.append(item)
                seen_items.add(item.item_id)

        return candidates[: self.candidate_size]

    def _get_tag_based_candidates(self, interest_tags: Dict[str, float]) -> List[RecommendItem]:
        candidates = []
        for tag, weight in sorted(interest_tags.items(), key=lambda x: x[1], reverse=True)[:20]:
            for item_type, items in self.item_pool.items():
                for item in items:
                    if tag in item.tags or tag == item.category:
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
                            score=weight * 0.5,
                            reason=f"基于您的兴趣标签: {tag}",
                        )
                        candidates.append(item_copy)
        return candidates

    def _get_intent_based_candidates(self, recent_intent: str) -> List[RecommendItem]:
        candidates = []
        intent_parts = recent_intent.split("_", 1)
        if len(intent_parts) == 2:
            intent_type, intent_category = intent_parts
            weight = 1.5 if intent_type == "purchase_intent" else 1.0
            for items in self.item_pool.values():
                for item in items:
                    if item.category == intent_category or intent_category in item.tags:
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
                            score=weight,
                            reason=f"根据您近期的{intent_type}意图推荐",
                        )
                        candidates.append(item_copy)
        return candidates

    def _get_lifecycle_based_candidates(self, lifecycle: UserLifecycle) -> List[RecommendItem]:
        candidates = []
        lifecycle_strategies = {
            UserLifecycle.NEW: ["新人专享", "首单优惠", "热门商品"],
            UserLifecycle.ACTIVE: ["复购推荐", "新品上架", "会员专享"],
            UserLifecycle.AT_RISK: ["限时折扣", "专属优惠", "召回福利"],
            UserLifecycle.DORMANT: ["回归礼包", "大额优惠券", "精选爆款"],
            UserLifecycle.CHURNED: ["超级优惠", "回归奖励", "限时特惠"],
        }
        tags = lifecycle_strategies.get(lifecycle, [])
        for items in self.item_pool.values():
            for item in items:
                for tag in tags:
                    if tag in item.tags:
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
                            score=0.8,
                            reason=f"为{lifecycle.value}阶段用户专属推荐",
                        )
                        candidates.append(item_copy)
                        break
        return candidates

    def _get_popular_candidates(self) -> List[RecommendItem]:
        candidates = []
        for items in self.item_pool.values():
            for item in items:
                if "热门" in item.tags or "爆款" in item.tags:
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
                        score=0.6,
                        reason="热门推荐",
                    )
                    candidates.append(item_copy)
        return candidates

    def _get_new_items_candidates(self) -> List[RecommendItem]:
        candidates = []
        for items in self.item_pool.values():
            for item in items:
                if "新品" in item.tags or "新上架" in item.tags:
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
                        score=0.5,
                        reason="新品推荐",
                    )
                    candidates.append(item_copy)
        return candidates

    def _score_and_rank(
        self, candidates: List[RecommendItem], profile: UserProfile
    ) -> List[RecommendItem]:
        for item in candidates:
            ctr_score = self._predict_ctr(item, profile)
            price_score = self._calculate_price_score(item, profile)
            diversity_score = self._calculate_diversity_score(item, profile)
            freshness_score = self._calculate_freshness_score(item)

            total_score = (
                0.5 * ctr_score
                + 0.2 * price_score
                + 0.15 * diversity_score
                + 0.15 * freshness_score
                + item.score
            )

            item.predicted_ctr = ctr_score
            item.score = total_score

        return sorted(candidates, key=lambda x: x.score, reverse=True)

    def _predict_ctr(self, item: RecommendItem, profile: UserProfile) -> float:
        base_ctr = 0.02
        tag_match_score = 0.0
        for tag in item.tags:
            if tag in profile.interest_tags:
                tag_match_score += profile.interest_tags[tag] * 0.01

        category_match = 0.0
        if item.category in profile.browse_categories:
            category_match = 0.02

        lifecycle_bonus = {
            UserLifecycle.NEW: 0.01,
            UserLifecycle.ACTIVE: 0.02,
            UserLifecycle.AT_RISK: 0.015,
            UserLifecycle.DORMANT: 0.005,
            UserLifecycle.CHURNED: 0.003,
        }
        lifecycle_score = lifecycle_bonus.get(profile.lifecycle_stage, 0.0)

        predicted_ctr = min(base_ctr + tag_match_score + category_match + lifecycle_score, 0.3)
        return max(predicted_ctr, self.ctr_threshold)

    def _calculate_price_score(self, item: RecommendItem, profile: UserProfile) -> float:
        if not item.price or not profile.preferred_price_range:
            return 0.5

        min_price, max_price = profile.preferred_price_range
        if min_price <= item.price <= max_price:
            return 1.0
        elif item.price < min_price:
            return item.price / min_price if min_price > 0 else 0.5
        else:
            return max_price / item.price if item.price > 0 else 0.5

    def _calculate_diversity_score(self, item: RecommendItem, profile: UserProfile) -> float:
        if not profile.browse_categories:
            return 0.5

        if item.category not in profile.browse_categories[-10:]:
            return 0.8
        return 0.3

    def _calculate_freshness_score(self, item: RecommendItem) -> float:
        if "新品" in item.tags or "新上架" in item.tags:
            return 0.9
        if "限时" in item.tags or "优惠" in item.tags:
            return 0.7
        return 0.5

    def _diversify_and_select(self, candidates: List[RecommendItem]) -> List[RecommendItem]:
        selected = []
        type_counts = defaultdict(int)
        category_counts = defaultdict(int)
        max_per_type = max(1, self.final_count // 3)
        max_per_category = 2

        for item in candidates:
            if len(selected) >= self.final_count:
                break

            if type_counts[item.item_type.value] >= max_per_type:
                continue

            if item.category and category_counts[item.category] >= max_per_category:
                continue

            selected.append(item)
            type_counts[item.item_type.value] += 1
            if item.category:
                category_counts[item.category] += 1

        while len(selected) < self.final_count and candidates:
            for item in candidates:
                if item not in selected:
                    selected.append(item)
                    break

        return selected[: self.final_count]

    def _get_cold_start_recommendations(self) -> List[RecommendItem]:
        recommendations = []
        default_tags = RECOMMENDATION_CONFIG["cold_start_default_tags"]
        for items in self.item_pool.values():
            for item in items:
                if any(tag in item.tags for tag in default_tags):
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
                        score=0.5,
                        reason="精选推荐",
                    )
                    recommendations.append(item_copy)
                    if len(recommendations) >= self.final_count:
                        break
            if len(recommendations) >= self.final_count:
                break
        return recommendations

    def _cache_recommendations(self, user_id: str, recommendations: List[RecommendItem]):
        try:
            key = f"recommendations:{user_id}"
            data = [
                {
                    "item_id": item.item_id,
                    "item_type": item.item_type.value,
                    "title": item.title,
                    "image_url": item.image_url,
                    "target_url": item.target_url,
                    "price": item.price,
                    "original_price": item.original_price,
                    "tags": item.tags,
                    "category": item.category,
                    "brand": item.brand,
                    "predicted_ctr": item.predicted_ctr,
                    "score": item.score,
                    "reason": item.reason,
                }
                for item in recommendations
            ]
            self.redis_client.setex(key, timedelta(hours=6), json.dumps(data))
        except Exception as e:
            LoggerManager.log_error(
                "recommendation", "cache_recommendations", e, user_id=user_id
            )

    def get_cached_recommendations(self, user_id: str) -> Optional[List[RecommendItem]]:
        try:
            key = f"recommendations:{user_id}"
            data = self.redis_client.get(key)
            if data:
                items_data = json.loads(data)
                return [
                    RecommendItem(
                        item_id=item["item_id"],
                        item_type=RecommendType(item["item_type"]),
                        title=item["title"],
                        image_url=item.get("image_url"),
                        target_url=item.get("target_url"),
                        price=item.get("price"),
                        original_price=item.get("original_price"),
                        tags=item.get("tags", []),
                        category=item.get("category"),
                        brand=item.get("brand"),
                        predicted_ctr=item.get("predicted_ctr", 0.0),
                        score=item.get("score", 0.0),
                        reason=item.get("reason"),
                    )
                    for item in items_data
                ]
        except Exception as e:
            LoggerManager.log_error(
                "recommendation", "get_cached_recommendations", e, user_id=user_id
            )
        return None

    def batch_generate(self, user_profiles: Dict[str, UserProfile]) -> Dict[str, List[RecommendItem]]:
        results = {}
        for user_id, profile in user_profiles.items():
            results[user_id] = self.generate_recommendations(user_id, profile)
        return results

    def add_item_to_pool(self, item: RecommendItem):
        try:
            if item.item_type.value not in self.item_pool:
                self.item_pool[item.item_type.value] = []
            self.item_pool[item.item_type.value].append(item)

            key = f"item_pool:{item.item_type.value}"
            existing = self.redis_client.get(key)
            items_list = json.loads(existing) if existing else []
            items_list.append(
                {
                    "item_id": item.item_id,
                    "item_type": item.item_type.value,
                    "title": item.title,
                    "image_url": item.image_url,
                    "target_url": item.target_url,
                    "price": item.price,
                    "original_price": item.original_price,
                    "tags": item.tags,
                    "category": item.category,
                    "brand": item.brand,
                }
            )
            self.redis_client.set(key, json.dumps(items_list))
        except Exception as e:
            LoggerManager.log_error("recommendation", "add_item_to_pool", e)
