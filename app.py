import os
import json
import uuid
import random
from datetime import datetime, timedelta
from collections import defaultdict
from io import BytesIO

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, make_response

app = Flask(__name__)
app.secret_key = "recommendation-system-secret-key-2024"
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
os.makedirs(REPORT_DIR, exist_ok=True)


# ==========================================
# 内存数据存储
# ==========================================
class InMemoryDB:
    def __init__(self):
        self.users = {}
        self.user_behaviors = defaultdict(list)
        self.user_profiles = {}
        self.recommendations = {}
        self.items_pool = []
        self.rules = []
        self.push_records = []
        self.stats = {
            "impressions": defaultdict(int),
            "clicks": defaultdict(int),
            "conversions": defaultdict(int),
            "revenue": defaultdict(float),
            "item_clicks": defaultdict(int),
        }
        self.ab_groups = {}

db = InMemoryDB()


# ==========================================
# 数据模型
# ==========================================
BEHAVIOR_TYPES = ["view", "click", "add_to_cart", "purchase", "search", "collect"]
CHANNELS = ["website", "app", "miniprogram"]
ITEM_TYPES = ["product", "content", "activity"]
LIFECYCLE_STAGES = ["new", "active", "at_risk", "dormant", "churned"]
PUSH_CHANNELS = ["email", "in_app", "sms"]
PUSH_STATUSES = ["pending", "sent", "opened", "clicked", "converted", "failed"]

CATEGORIES = ["电子产品", "服装", "食品", "家居", "美妆", "图书", "运动", "母婴", "数码", "汽车用品"]
BRANDS = ["苹果", "华为", "小米", "耐克", "阿迪达斯", "宝洁", "联合利华", "三只松鼠", "良品铺子"]
TAGS_POOL = [
    "新品", "热门", "爆款", "限时优惠", "折扣", "新人专享",
    "会员专享", "精选推荐", "热销榜单", "复购推荐", "节日特惠",
    "限时折扣", "满减活动", "包邮", "赠品", "限量款",
]


# ==========================================
# 初始化数据
# ==========================================
def init_item_pool():
    items = []
    item_id = 0

    for cat_idx, category in enumerate(CATEGORIES):
        for i in range(6):
            item_type = ITEM_TYPES[item_id % 3]
            brand = BRANDS[item_id % len(BRANDS)]
            base_price = 50 + (item_id * 37) % 950
            price = round(base_price * random.uniform(0.8, 1.0), 2)
            original_price = round(price * random.uniform(1.1, 1.5), 2)

            if item_type == "product":
                title = f"{brand} {category}商品{item_id + 1}"
            elif item_type == "content":
                title = f"{category}选购指南 - 第{item_id + 1}期"
            else:
                title = f"{category}专场促销活动 #{item_id + 1}"

            tags = random.sample(TAGS_POOL, k=random.randint(2, 4))
            tags.append(category)

            items.append({
                "item_id": f"ITEM_{item_id:06d}",
                "item_type": item_type,
                "title": title,
                "category": category,
                "brand": brand,
                "price": price,
                "original_price": original_price,
                "tags": tags,
                "image_url": f"https://picsum.photos/seed/item{item_id}/200/200",
                "target_url": f"/item/{item_id:06d}",
            })
            item_id += 1

    db.items_pool = items
    print(f"✅ 已初始化 {len(items)} 条商品数据")


def init_users():
    for i in range(200):
        user_id = f"USER_{i:06d}"
        db.users[user_id] = {
            "user_id": user_id,
            "email": f"user{i}@example.com",
            "phone": f"138{i:08d}",
            "registered_at": (datetime.now() - timedelta(days=random.randint(0, 365))).isoformat(),
        }
    print(f"✅ 已初始化 {len(db.users)} 个用户")


def simulate_behavior_events(count=1000):
    user_ids = list(db.users.keys())
    now = datetime.now()

    for i in range(count):
        user_id = random.choice(user_ids)
        behavior_type = random.choices(
            BEHAVIOR_TYPES,
            weights=[0.4, 0.25, 0.15, 0.05, 0.1, 0.05]
        )[0]
        channel = random.choice(CHANNELS)
        item = random.choice(db.items_pool) if random.random() > 0.1 else None

        event = {
            "event_id": str(uuid.uuid4()),
            "user_id": user_id,
            "behavior_type": behavior_type,
            "channel": channel,
            "item_id": item["item_id"] if item else None,
            "item_type": item["item_type"] if item else None,
            "category": item["category"] if item else random.choice(CATEGORIES),
            "price": item["price"] if item and behavior_type in ["purchase", "add_to_cart"] else None,
            "quantity": random.randint(1, 3) if behavior_type == "purchase" else None,
            "timestamp": (now - timedelta(minutes=random.randint(0, 60 * 24 * 7))).isoformat(),
            "session_id": str(uuid.uuid4()),
        }
        db.user_behaviors[user_id].append(event)
        _update_stats_from_event(event)

    print(f"✅ 已模拟 {count} 条用户行为数据")
    _build_all_user_profiles()


def _update_stats_from_event(event):
    date_key = event["timestamp"][:10].replace("-", "")
    db.stats["impressions"][f"{date_key}:total"] += 1
    db.stats["impressions"][f"{date_key}:{event['channel']}"] += 1

    if event["behavior_type"] in ["click", "purchase"]:
        db.stats["clicks"][f"{date_key}:total"] += 1
        db.stats["clicks"][f"{date_key}:{event['channel']}"] += 1
        if event["item_id"]:
            db.stats["item_clicks"][event["item_id"]] += 1

    if event["behavior_type"] == "purchase" and event["price"]:
        revenue = event["price"] * (event["quantity"] or 1)
        db.stats["conversions"][f"{date_key}:total"] += 1
        db.stats["conversions"][f"{date_key}:{event['channel']}"] += 1
        db.stats["revenue"][f"{date_key}:total"] += revenue
        db.stats["revenue"][f"{date_key}:{event['channel']}"] += revenue


# ==========================================
# 用户画像计算
# ==========================================
def _build_user_profile(user_id):
    behaviors = db.user_behaviors.get(user_id, [])
    now = datetime.now()

    interest_tags = defaultdict(float)
    total_orders = 0
    total_spent = 0.0
    last_active_time = None
    first_active_time = None
    last_purchase_time = None
    browse_categories = set()
    prices = []

    behavior_weights = {
        "view": 1.0, "click": 2.0, "collect": 4.0,
        "share": 3.0, "add_to_cart": 5.0, "purchase": 10.0, "search": 1.5
    }

    for bh in behaviors:
        ts = datetime.fromisoformat(bh["timestamp"])
        if first_active_time is None or ts < first_active_time:
            first_active_time = ts
        if last_active_time is None or ts > last_active_time:
            last_active_time = ts

        weight = behavior_weights.get(bh["behavior_type"], 1.0)
        decay = 0.95 ** ((now - ts).days)

        if bh["category"]:
            interest_tags[bh["category"]] += weight * decay

        if bh.get("item_type"):
            interest_tags[bh["item_type"]] += weight * decay * 0.5

        if bh["behavior_type"] == "purchase" and bh.get("price"):
            total_orders += 1
            qty = bh.get("quantity", 1) or 1
            total_spent += bh["price"] * qty
            last_purchase_time = ts
            prices.append(bh["price"])

        if bh["category"]:
            browse_categories.add(bh["category"])
            if bh.get("price") and bh["behavior_type"] in ["view", "add_to_cart", "purchase"]:
                prices.append(bh["price"])

    days_since_first = (now - first_active_time).days if first_active_time else 0
    days_since_active = (now - last_active_time).days if last_active_time else 999
    days_since_purchase = (now - last_purchase_time).days if last_purchase_time else 999

    if days_since_first <= 7 and total_orders == 0:
        lifecycle = "new"
    elif total_orders > 0 and days_since_purchase <= 30:
        lifecycle = "active"
    elif days_since_active <= 7:
        lifecycle = "active"
    elif 7 < days_since_active <= 30:
        lifecycle = "at_risk"
    elif 30 < days_since_active <= 90:
        lifecycle = "dormant"
    else:
        lifecycle = "churned"

    avg_order_value = total_spent / total_orders if total_orders > 0 else 0.0
    purchase_frequency = (total_orders / max(days_since_first, 1)) * 30 if days_since_first > 0 else 0

    preferred_price_range = None
    if len(prices) >= 3:
        avg_price = sum(prices) / len(prices)
        preferred_price_range = (avg_price * 0.7, avg_price * 1.3)

    sorted_tags = sorted(interest_tags.items(), key=lambda x: x[1], reverse=True)[:50]

    profile = {
        "user_id": user_id,
        "interest_tags": dict(sorted_tags),
        "lifecycle_stage": lifecycle,
        "recent_intent": None,
        "intent_confidence": 0.0,
        "first_active_time": first_active_time.isoformat() if first_active_time else None,
        "last_active_time": last_active_time.isoformat() if last_active_time else None,
        "total_orders": total_orders,
        "total_spent": total_spent,
        "avg_order_value": avg_order_value,
        "purchase_frequency": purchase_frequency,
        "last_purchase_time": last_purchase_time.isoformat() if last_purchase_time else None,
        "browse_categories": list(browse_categories),
        "preferred_price_range": list(preferred_price_range) if preferred_price_range else None,
    }

    recent_behaviors = [
        bh for bh in behaviors
        if (now - datetime.fromisoformat(bh["timestamp"])).total_seconds() < 24 * 3600
    ]
    for bh in recent_behaviors:
        if bh["behavior_type"] in ["add_to_cart", "search"] and bh["category"]:
            profile["recent_intent"] = f"intent_{bh['category']}"
            profile["intent_confidence"] = min(profile["intent_confidence"] + 0.3, 1.0)

    db.user_profiles[user_id] = profile
    return profile


def _build_all_user_profiles():
    for user_id in db.users.keys():
        _build_user_profile(user_id)
    print(f"✅ 已构建 {len(db.user_profiles)} 个用户画像")


# ==========================================
# 推荐算法
# ==========================================
def generate_recommendations(user_id, count=10):
    profile = db.user_profiles.get(user_id)
    if not profile:
        profile = _build_user_profile(user_id)

    candidates = []
    seen = set()

    interest_tags = profile["interest_tags"]
    for tag, weight in sorted(interest_tags.items(), key=lambda x: x[1], reverse=True)[:20]:
        for item in db.items_pool:
            if item["item_id"] in seen:
                continue
            if tag in item["tags"] or tag == item["category"]:
                item_copy = item.copy()
                item_copy["score"] = weight * 0.5
                item_copy["reason"] = f"基于您的兴趣：{tag}"
                candidates.append(item_copy)
                seen.add(item["item_id"])

    if profile["recent_intent"]:
        parts = profile["recent_intent"].split("_", 1)
        if len(parts) == 2:
            intent_cat = parts[1]
            for item in db.items_pool:
                if item["item_id"] in seen:
                    continue
                if item["category"] == intent_cat:
                    item_copy = item.copy()
                    item_copy["score"] = 1.5
                    item_copy["reason"] = "根据您近期的浏览意向"
                    candidates.append(item_copy)
                    seen.add(item["item_id"])

    lifecycle_items = {
        "new": ["新人专享", "首单优惠"],
        "active": ["复购推荐", "会员专享"],
        "at_risk": ["限时折扣", "专属优惠"],
        "dormant": ["回归礼包", "大额券"],
        "churned": ["超级优惠", "回归奖励"],
    }
    target_tags = lifecycle_items.get(profile["lifecycle_stage"], [])
    for item in db.items_pool:
        if item["item_id"] in seen:
            continue
        if any(tag in item["tags"] for tag in target_tags):
            item_copy = item.copy()
            item_copy["score"] = 0.8
            item_copy["reason"] = f"{profile['lifecycle_stage']}用户专属"
            candidates.append(item_copy)
            seen.add(item["item_id"])

    for item in db.items_pool:
        if item["item_id"] in seen:
            continue
        if "热门" in item["tags"] or "爆款" in item["tags"]:
            item_copy = item.copy()
            item_copy["score"] = 0.6
            item_copy["reason"] = "热门推荐"
            candidates.append(item_copy)
            seen.add(item["item_id"])

    for item in db.items_pool:
        if item["item_id"] in seen:
            continue
        if "新品" in item["tags"]:
            item_copy = item.copy()
            item_copy["score"] = 0.5
            item_copy["reason"] = "新品推荐"
            candidates.append(item_copy)
            seen.add(item["item_id"])

    for item in candidates:
        base_ctr = 0.02
        tag_match = sum(
            profile["interest_tags"].get(tag, 0) * 0.01
            for tag in item["tags"]
            if tag in profile["interest_tags"]
        )
        cat_match = 0.02 if item["category"] in profile["browse_categories"] else 0.0
        predicted_ctr = min(base_ctr + tag_match + cat_match, 0.3)

        price_score = 0.5
        if item["price"] and profile["preferred_price_range"]:
            pmin, pmax = profile["preferred_price_range"]
            if pmin <= item["price"] <= pmax:
                price_score = 1.0
            elif item["price"] < pmin and pmin > 0:
                price_score = item["price"] / pmin
            elif item["price"] > pmax and item["price"] > 0:
                price_score = pmax / item["price"]

        diversity = 0.3 if item["category"] in profile["browse_categories"][-10:] else 0.8
        freshness = 0.9 if "新品" in item["tags"] else (0.7 if "限时" in item["tags"] else 0.5)

        total_score = (
            0.5 * predicted_ctr * 100
            + 0.2 * price_score
            + 0.15 * diversity
            + 0.15 * freshness
            + item["score"]
        )

        item["predicted_ctr"] = predicted_ctr
        item["score"] = total_score

    candidates.sort(key=lambda x: x["score"], reverse=True)

    selected = []
    type_count = defaultdict(int)
    cat_count = defaultdict(int)
    max_per_type = max(1, count // 3)
    max_per_cat = 2

    for item in candidates:
        if len(selected) >= count:
            break
        if type_count[item["item_type"]] >= max_per_type:
            continue
        if cat_count[item["category"]] >= max_per_cat:
            continue
        selected.append(item)
        type_count[item["item_type"]] += 1
        cat_count[item["category"]] += 1

    for item in candidates:
        if len(selected) >= count:
            break
        if item not in selected:
            selected.append(item)

    selected = selected[:count]
    selected = apply_rules_to_recommendations(profile, selected)

    db.recommendations[user_id] = {
        "items": selected,
        "generated_at": datetime.now().isoformat(),
        "count": len(selected),
        "avg_ctr": sum(item["predicted_ctr"] for item in selected) / len(selected) if selected else 0,
    }

    return selected


def generate_all_user_recommendations():
    for user_id in db.users.keys():
        generate_recommendations(user_id)
    print(f"✅ 已为 {len(db.recommendations)} 个用户生成推荐")


# ==========================================
# 规则引擎
# ==========================================
def apply_rules_to_recommendations(profile, recommendations):
    now = datetime.now()
    modified = [item.copy() for item in recommendations]

    for rule in db.rules:
        if not rule["enabled"]:
            continue
        if rule["start_time"] and now < datetime.fromisoformat(rule["start_time"]):
            continue
        if rule["end_time"] and now > datetime.fromisoformat(rule["end_time"]):
            continue
        if not _rule_matches(profile, rule["conditions"]):
            continue

        actions = rule["actions"]
        if "boost_tags" in actions:
            boost_tags = actions["boost_tags"]
            boost_weight = actions.get("boost_weight", 2.0)
            for item in modified:
                if any(tag in item["tags"] for tag in boost_tags):
                    item["score"] *= boost_weight
                    item["reason"] = actions.get("custom_message", item.get("reason", ""))

        if "force_include_tags" in actions:
            include_tags = actions["force_include_tags"]
            max_include = actions.get("max_include", 3)
            force_items = []
            for item in db.items_pool:
                if any(tag in item["tags"] for tag in include_tags):
                    if item["item_id"] not in [i["item_id"] for i in modified]:
                        item_copy = item.copy()
                        item_copy["score"] = 999
                        item_copy["reason"] = f"规则推荐: {rule['name']}"
                        item_copy["predicted_ctr"] = 0.05
                        force_items.append(item_copy)
                        if len(force_items) >= max_include:
                            break
            modified = force_items + modified

    modified.sort(key=lambda x: x["score"], reverse=True)
    return modified


def _rule_matches(profile, conditions):
    if "lifecycle_stage" in conditions:
        target = conditions["lifecycle_stage"]
        if isinstance(target, str) and profile["lifecycle_stage"] != target:
            return False
        if isinstance(target, list) and profile["lifecycle_stage"] not in target:
            return False

    if "user_tags" in conditions:
        tags = conditions["user_tags"]
        if isinstance(tags, str):
            tags = [tags]
        if not any(tag in profile["interest_tags"] for tag in tags):
            return False

    if "min_total_spent" in conditions:
        if profile["total_spent"] < conditions["min_total_spent"]:
            return False

    if "min_orders" in conditions:
        if profile["total_orders"] < conditions["min_orders"]:
            return False

    return True


# ==========================================
# 推送与效果追踪
# ==========================================
def create_push_records():
    now = datetime.now()
    for user_id in list(db.users.keys())[:100]:
        if user_id not in db.recommendations:
            continue

        ab_group = "treatment" if hash(user_id) % 10 < 7 else "control"

        if ab_group == "treatment":
            status = random.choices(
                ["sent", "opened", "clicked", "converted"],
                weights=[0.3, 0.25, 0.3, 0.15]
            )[0]
        else:
            status = "sent"

        click_time = None
        open_time = None
        conversion_time = None
        conversion_value = 0.0

        send_time = now - timedelta(minutes=random.randint(0, 60 * 24))

        if status in ["opened", "clicked", "converted"]:
            open_time = (send_time + timedelta(minutes=random.randint(1, 120))).isoformat()
        if status in ["clicked", "converted"]:
            click_time = (send_time + timedelta(minutes=random.randint(2, 180))).isoformat()
        if status == "converted":
            conversion_time = (send_time + timedelta(minutes=random.randint(10, 360))).isoformat()
            conversion_value = random.uniform(50, 500)

        record = {
            "push_id": str(uuid.uuid4()),
            "user_id": user_id,
            "channel": random.choice(PUSH_CHANNELS),
            "recommend_items": db.recommendations[user_id]["items"][:5],
            "send_time": send_time.isoformat(),
            "status": status,
            "open_time": open_time,
            "click_time": click_time,
            "conversion_time": conversion_time,
            "conversion_value": conversion_value,
            "ab_test_group": ab_group,
        }
        db.push_records.append(record)

    for _ in range(50):
        user_id = random.choice(list(db.users.keys()))
        item = random.choice(db.items_pool)
        db.stats["clicks"]["total:total"] += 1
        db.stats["item_clicks"][item["item_id"]] += 1

    print(f"✅ 已创建 {len(db.push_records)} 条推送记录")


# ==========================================
# 统计与报告
# ==========================================
def get_summary_stats():
    total_impressions = sum(v for k, v in db.stats["impressions"].items() if k.endswith(":total"))
    total_clicks = sum(v for k, v in db.stats["clicks"].items() if k.endswith(":total"))
    total_conversions = sum(v for k, v in db.stats["conversions"].items() if k.endswith(":total"))
    total_revenue = sum(v for k, v in db.stats["revenue"].items() if k.endswith(":total"))

    ctr = total_clicks / total_impressions if total_impressions > 0 else 0
    cvr = total_conversions / total_clicks if total_clicks > 0 else 0
    avg_order_value = total_revenue / total_conversions if total_conversions > 0 else 0

    return {
        "total_users": len(db.users),
        "new_users": sum(1 for p in db.user_profiles.values() if p["lifecycle_stage"] == "new"),
        "total_impressions": total_impressions + len(db.push_records),
        "total_clicks": total_clicks,
        "total_conversions": total_conversions,
        "total_revenue": total_revenue + sum(r["conversion_value"] for r in db.push_records),
        "ctr": ctr,
        "cvr": cvr,
        "avg_order_value": avg_order_value,
        "rule_count": len([r for r in db.rules if r["enabled"]]),
        "item_pool_size": len(db.items_pool),
        "recommendation_count": len(db.recommendations),
        "behavior_count": sum(len(v) for v in db.user_behaviors.values()),
        "model_health": "good" if ctr >= 0.03 else "warning",
    }


def get_trend_data(days=7):
    dates = []
    ctr_values = []
    clicks_values = []
    revenue_values = []

    for i in range(days - 1, -1, -1):
        date = datetime.now() - timedelta(days=i)
        date_key = date.strftime("%Y%m%d")

        imp = db.stats["impressions"].get(f"{date_key}:total", 0)
        clk = db.stats["clicks"].get(f"{date_key}:total", 0)
        rev = db.stats["revenue"].get(f"{date_key}:total", 0.0)

        ctr = clk / imp if imp > 0 else 0.02 + random.uniform(-0.01, 0.01)
        clk = clk or random.randint(100, 500)
        rev = rev or random.uniform(5000, 20000)

        dates.append(date.strftime("%m-%d"))
        ctr_values.append(round(ctr * 100, 2))
        clicks_values.append(clk)
        revenue_values.append(round(rev, 2))

    return dates, ctr_values, clicks_values, revenue_values


def get_lifecycle_distribution():
    dist = {"new": 0, "active": 0, "at_risk": 0, "dormant": 0, "churned": 0}
    for profile in db.user_profiles.values():
        dist[profile["lifecycle_stage"]] += 1
    return dist


def get_top_items(n=10):
    item_clicks = [(iid, db.stats["item_clicks"].get(iid, random.randint(10, 200)))
                   for iid in [item["item_id"] for item in db.items_pool]]
    item_clicks.sort(key=lambda x: x[1], reverse=True)

    result = []
    for item_id, clicks in item_clicks[:n]:
        item = next((i for i in db.items_pool if i["item_id"] == item_id), None)
        if item:
            result.append({
                "item_id": item_id,
                "title": item["title"],
                "item_type": item["item_type"],
                "clicks": clicks,
            })
    return result


def get_ab_test_results():
    treatment = [r for r in db.push_records if r["ab_test_group"] == "treatment"]
    control = [r for r in db.push_records if r["ab_test_group"] == "control"]

    def calc_metrics(records):
        imp = len(records) or 1
        clk = sum(1 for r in records if r["status"] in ["clicked", "converted"])
        conv = sum(1 for r in records if r["status"] == "converted")
        rev = sum(r["conversion_value"] for r in records)
        return {
            "impressions": imp,
            "clicks": clk,
            "conversions": conv,
            "revenue": rev,
            "ctr": clk / imp,
            "cvr": conv / clk if clk > 0 else 0,
        }

    t_metrics = calc_metrics(treatment)
    c_metrics = calc_metrics(control)

    lift = {}
    if c_metrics["ctr"] > 0:
        lift["ctr"] = (t_metrics["ctr"] - c_metrics["ctr"]) / c_metrics["ctr"]
    if c_metrics["cvr"] > 0:
        lift["cvr"] = (t_metrics["cvr"] - c_metrics["cvr"]) / c_metrics["cvr"]
    if c_metrics["revenue"] > 0:
        lift["revenue"] = (t_metrics["revenue"] - c_metrics["revenue"]) / c_metrics["revenue"]

    return {
        "treatment": t_metrics,
        "control": c_metrics,
        "lift": lift,
    }


def generate_excel_report():
    dates, ctr_vals, clicks_vals, revenue_vals = get_trend_data(30)
    summary = get_summary_stats()

    trend_df = pd.DataFrame({
        "日期": dates,
        "CTR(%)": ctr_vals,
        "点击量": clicks_vals,
        "收入(元)": revenue_vals,
    })

    summary_data = [
        ["总用户数", summary["total_users"]],
        ["总曝光量", summary["total_impressions"]],
        ["总点击量", summary["total_clicks"]],
        ["点击率", f"{summary['ctr']*100:.2f}%"],
        ["转化率", f"{summary['cvr']*100:.2f}%"],
        ["总收入", f"¥{summary['total_revenue']:.2f}"],
        ["推荐规则数", summary["rule_count"]],
        ["商品池大小", summary["item_pool_size"]],
    ]
    summary_df = pd.DataFrame(summary_data, columns=["指标", "数值"])

    top_items = get_top_items(20)
    items_df = pd.DataFrame(top_items)

    users_data = []
    for uid, profile in list(db.user_profiles.items())[:50]:
        users_data.append({
            "用户ID": uid,
            "生命周期": profile["lifecycle_stage"],
            "总订单": profile["total_orders"],
            "总消费": f"¥{profile['total_spent']:.2f}",
            "兴趣标签数": len(profile["interest_tags"]),
        })
    users_df = pd.DataFrame(users_data)

    filename = f"report_{datetime.now().strftime('%Y%m%d')}.xlsx"
    filepath = os.path.join(REPORT_DIR, filename)

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="数据汇总", index=False)
        trend_df.to_excel(writer, sheet_name="趋势数据", index=False)
        items_df.to_excel(writer, sheet_name="热门商品", index=False)
        users_df.to_excel(writer, sheet_name="用户列表", index=False)

    return filename, filepath


# ==========================================
# Flask 路由
# ==========================================
@app.route("/")
def dashboard():
    stats = get_summary_stats()
    trend_dates, trend_ctr, trend_clicks, _ = get_trend_data(7)
    lifecycle_dist = get_lifecycle_distribution()
    top_items = get_top_items(10)

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        stats=stats,
        trend_dates=trend_dates,
        trend_ctr=trend_ctr,
        trend_clicks=trend_clicks,
        lifecycle_dist=lifecycle_dist,
        top_items=top_items,
    )


@app.route("/users")
def users_list():
    users = []
    for user_id, profile in db.user_profiles.items():
        top_tags = sorted(profile["interest_tags"].items(), key=lambda x: x[1], reverse=True)[:3]
        users.append({
            "user_id": user_id,
            "lifecycle_stage": profile["lifecycle_stage"],
            "top_tags": top_tags,
            "recent_intent": profile["recent_intent"],
            "total_orders": profile["total_orders"],
            "total_spent": profile["total_spent"],
            "last_active_time": profile["last_active_time"],
        })

    return render_template(
        "users.html",
        active_page="users",
        users=users,
    )


@app.route("/users/<user_id>")
def user_detail(user_id):
    profile = db.user_profiles.get(user_id)
    if not profile:
        flash("用户不存在", "error")
        return redirect(url_for("users_list"))

    recommendations = []
    if user_id in db.recommendations:
        recommendations = db.recommendations[user_id]["items"]

    behaviors = sorted(
        db.user_behaviors.get(user_id, []),
        key=lambda x: x["timestamp"],
        reverse=True
    )

    return render_template(
        "user_detail.html",
        active_page="users",
        user=profile,
        recommendations=recommendations,
        behaviors=behaviors,
    )


@app.route("/recommendations")
def recommendations_list():
    recommendations_data = {}
    for uid, data in db.recommendations.items():
        recommendations_data[uid] = {
            "count": data["count"],
            "avg_ctr": data["avg_ctr"],
            "generated_at": data["generated_at"][:16],
        }
    return render_template(
        "recommendations.html",
        active_page="recommendations",
        recommendations_data=recommendations_data,
    )


@app.route("/recommendations/generate/<user_id>")
def generate_recommendation(user_id):
    generate_recommendations(user_id)
    flash(f"已为用户 {user_id} 生成推荐", "success")
    return redirect(url_for("user_detail", user_id=user_id))


@app.route("/recommendations/generate-all")
def generate_all_recommendations():
    generate_all_user_recommendations()
    flash(f"已为所有用户生成推荐（共 {len(db.recommendations)} 个用户）", "success")
    return redirect(url_for("recommendations_list"))


@app.route("/rules")
def rules_list():
    return render_template(
        "rules.html",
        active_page="rules",
        rules=db.rules,
    )


def _create_rule_core(name, description, priority=10, conditions=None, actions=None,
                      start_time=None, end_time=None, enabled=True):
    rule = {
        "rule_id": str(uuid.uuid4()),
        "name": name,
        "description": description,
        "priority": priority,
        "conditions": conditions or {},
        "actions": actions or {},
        "start_time": start_time,
        "end_time": end_time,
        "enabled": enabled,
        "created_at": datetime.now().isoformat(),
    }
    db.rules.append(rule)
    db.rules.sort(key=lambda r: r["priority"], reverse=True)
    return rule


@app.route("/rules/create", methods=["GET", "POST"])
def create_rule_page():
    if request.method == "POST":
        name = request.form.get("name", "")
        description = request.form.get("description", "")
        priority = int(request.form.get("priority", 10))

        conditions = {}
        if request.form.get("lifecycle_stage"):
            conditions["lifecycle_stage"] = request.form["lifecycle_stage"]
        if request.form.get("user_tags"):
            conditions["user_tags"] = [t.strip() for t in request.form["user_tags"].split(",") if t.strip()]
        if request.form.get("min_total_spent"):
            conditions["min_total_spent"] = float(request.form["min_total_spent"])
        if request.form.get("min_orders"):
            conditions["min_orders"] = int(request.form["min_orders"])

        actions = {}
        if request.form.get("boost_tags"):
            actions["boost_tags"] = [t.strip() for t in request.form["boost_tags"].split(",") if t.strip()]
            actions["boost_weight"] = float(request.form.get("boost_weight", 2.0))
        if request.form.get("force_include_tags"):
            actions["force_include_tags"] = [t.strip() for t in request.form["force_include_tags"].split(",") if t.strip()]
            actions["max_include"] = int(request.form.get("max_include", 3))
        if request.form.get("custom_message"):
            actions["custom_message"] = request.form["custom_message"]

        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")

        _create_rule_core(
            name=name,
            description=description,
            priority=priority,
            conditions=conditions,
            actions=actions,
            start_time=start_time,
            end_time=end_time,
        )

        flash(f"规则 '{name}' 创建成功", "success")
        return redirect(url_for("rules_list"))

    return render_template(
        "create_rule.html",
        active_page="rules",
    )


@app.route("/rules/toggle/<rule_id>")
def toggle_rule(rule_id):
    for rule in db.rules:
        if rule["rule_id"] == rule_id:
            rule["enabled"] = not rule["enabled"]
            flash(f"规则 '{rule['name']}' 已{'启用' if rule['enabled'] else '禁用'}", "success")
            break
    return redirect(url_for("rules_list"))


@app.route("/rules/delete/<rule_id>")
def delete_rule(rule_id):
    db.rules = [r for r in db.rules if r["rule_id"] != rule_id]
    flash("规则已删除", "success")
    return redirect(url_for("rules_list"))


@app.route("/report")
def report_view():
    summary = get_summary_stats()
    trend_dates, trend_ctr, _, trend_revenue = get_trend_data(30)
    ab_results = get_ab_test_results()

    channel_labels = ["邮件", "站内信", "短信"]
    channel_impressions = [
        sum(1 for r in db.push_records if r["channel"] == "email"),
        sum(1 for r in db.push_records if r["channel"] == "in_app"),
        sum(1 for r in db.push_records if r["channel"] == "sms"),
    ]
    channel_clicks = [
        sum(1 for r in db.push_records if r["channel"] == "email" and r["status"] in ["clicked", "converted"]),
        sum(1 for r in db.push_records if r["channel"] == "in_app" and r["status"] in ["clicked", "converted"]),
        sum(1 for r in db.push_records if r["channel"] == "sms" and r["status"] in ["clicked", "converted"]),
    ]

    ab_ctr = [ab_results["control"]["ctr"] * 100, ab_results["treatment"]["ctr"] * 100]
    ab_cvr = [ab_results["control"]["cvr"] * 100, ab_results["treatment"]["cvr"] * 100]

    current_ctr = summary["ctr"]
    benchmark_ctr = 0.03
    model_alert = {
        "needs_adjustment": current_ctr < benchmark_ctr,
        "below_benchmark_days": 2 if current_ctr < benchmark_ctr else 0,
    }

    report_file = request.args.get("report_file")

    return render_template(
        "report.html",
        active_page="report",
        summary=summary,
        trend_dates=trend_dates,
        trend_ctr=trend_ctr,
        trend_revenue=trend_revenue,
        channel_labels=channel_labels,
        channel_impressions=channel_impressions,
        channel_clicks=channel_clicks,
        ab_results=ab_results,
        ab_ctr=ab_ctr,
        ab_cvr=ab_cvr,
        current_ctr=current_ctr,
        benchmark_ctr=benchmark_ctr,
        model_alert=model_alert,
        report_file=report_file,
    )


@app.route("/report/generate")
def generate_report():
    filename, filepath = generate_excel_report()
    flash(f"报告已生成: {filename}", "success")
    return redirect(url_for("report_view", report_file=filename))


@app.route("/report/download/<filename>")
def download_report(filename):
    filepath = os.path.join(REPORT_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    else:
        flash("报告文件不存在", "error")
        return redirect(url_for("report_view"))


@app.route("/query")
def query_page():
    query_params = {
        "user_id": request.args.get("user_id", ""),
        "channel": request.args.get("channel", ""),
        "start_date": request.args.get("start_date", ""),
        "end_date": request.args.get("end_date", ""),
    }

    records = None
    if any(query_params.values()) or request.args:
        records = []
        for record in db.push_records:
            if query_params["user_id"] and record["user_id"] != query_params["user_id"]:
                continue
            if query_params["channel"] and record["channel"] != query_params["channel"]:
                continue
            if query_params["start_date"] and record["send_time"] < query_params["start_date"]:
                continue
            if query_params["end_date"] and record["send_time"] > query_params["end_date"] + " 23:59:59":
                continue
            records.append(record)

    records_json = json.dumps(records) if records else "[]"

    return render_template(
        "query.html",
        active_page="query",
        query_params=query_params,
        records=records if records is not None else None,
        records_json=records_json,
    )


@app.route("/query/export")
def export_query():
    query_params = {
        "user_id": request.args.get("user_id", ""),
        "channel": request.args.get("channel", ""),
        "start_date": request.args.get("start_date", ""),
        "end_date": request.args.get("end_date", ""),
    }

    records = []
    for record in db.push_records:
        if query_params["user_id"] and record["user_id"] != query_params["user_id"]:
            continue
        if query_params["channel"] and record["channel"] != query_params["channel"]:
            continue
        if query_params["start_date"] and record["send_time"] < query_params["start_date"]:
            continue
        if query_params["end_date"] and record["send_time"] > query_params["end_date"] + " 23:59:59":
            continue
        records.append({
            "推送ID": record["push_id"],
            "用户ID": record["user_id"],
            "渠道": record["channel"],
            "状态": record["status"],
            "发送时间": record["send_time"],
            "打开时间": record["open_time"] or "",
            "点击时间": record["click_time"] or "",
            "转化时间": record["conversion_time"] or "",
            "转化价值": record["conversion_value"],
            "AB测试组": record["ab_test_group"] or "",
        })

    df = pd.DataFrame(records)
    output = BytesIO()
    df.to_excel(output, index=False, engine="openpyxl")
    output.seek(0)

    filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/simulate-behavior")
def simulate_behavior():
    simulate_behavior_events(500)
    flash("已模拟 500 条新的用户行为数据", "success")
    return redirect(url_for("dashboard"))


# ==========================================
# 初始化并运行
# ==========================================
def init_system():
    print("\n" + "=" * 60)
    print("🚀 正在初始化企业级用户行为分析与个性化推荐系统...")
    print("=" * 60 + "\n")

    init_item_pool()
    init_users()
    simulate_behavior_events(1000)
    generate_all_user_recommendations()
    create_push_records()

    _create_rule_core(
        name="新用户专享优惠",
        description="针对新用户的专属优惠推荐",
        priority=100,
        conditions={"lifecycle_stage": "new"},
        actions={
            "boost_tags": ["新人专享", "首单优惠"],
            "boost_weight": 3.0,
            "force_include_tags": ["新人专享"],
            "max_include": 3,
            "custom_message": "新用户专享，首单立减！",
        },
    )

    _create_rule_core(
        name="活跃用户复购推荐",
        description="针对活跃用户的复购引导",
        priority=80,
        conditions={"lifecycle_stage": "active", "min_orders": 1},
        actions={
            "boost_tags": ["复购推荐", "会员专享"],
            "boost_weight": 2.0,
            "custom_message": "老顾客专属，精选好物！",
        },
    )

    _create_rule_core(
        name="流失用户召回",
        description="针对流失风险用户的召回策略",
        priority=90,
        conditions={"lifecycle_stage": "at_risk"},
        actions={
            "boost_tags": ["限时折扣", "专属优惠"],
            "boost_weight": 2.5,
            "force_include_tags": ["限时折扣"],
            "max_include": 5,
            "custom_message": "好久不见，专属优惠送给您！",
        },
    )

    print("\n" + "=" * 60)
    print("✅ 系统初始化完成！")
    print(f"   - 用户数: {len(db.users)}")
    print(f"   - 商品数: {len(db.items_pool)}")
    print(f"   - 行为记录: {sum(len(v) for v in db.user_behaviors.values())}")
    print(f"   - 推荐结果: {len(db.recommendations)}")
    print(f"   - 推送记录: {len(db.push_records)}")
    print(f"   - 推荐规则: {len(db.rules)}")
    print("=" * 60)
    print("\n🌐 访问 http://localhost:5000 打开Web界面\n")


if __name__ == "__main__":
    init_system()
    app.run(debug=True, host="0.0.0.0", port=5000)
