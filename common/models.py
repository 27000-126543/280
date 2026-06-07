from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from enum import Enum


class BehaviorType(Enum):
    VIEW = "view"
    CLICK = "click"
    ADD_TO_CART = "add_to_cart"
    PURCHASE = "purchase"
    SEARCH = "search"
    SHARE = "share"
    COLLECT = "collect"


class ChannelType(Enum):
    WEBSITE = "website"
    APP = "app"
    MINIPROGRAM = "miniprogram"


class PushChannel(Enum):
    EMAIL = "email"
    IN_APP = "in_app"
    SMS = "sms"


class UserLifecycle(Enum):
    NEW = "new"
    ACTIVE = "active"
    AT_RISK = "at_risk"
    CHURNED = "churned"
    DORMANT = "dormant"


class RecommendType(Enum):
    PRODUCT = "product"
    CONTENT = "content"
    ACTIVITY = "activity"


@dataclass
class UserBehaviorEvent:
    event_id: str
    user_id: str
    behavior_type: BehaviorType
    channel: ChannelType
    item_id: Optional[str] = None
    item_type: Optional[str] = None
    category: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    session_id: Optional[str] = None
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    duration: Optional[float] = None
    price: Optional[float] = None
    quantity: Optional[int] = None
    extra: Dict = field(default_factory=dict)


@dataclass
class UserProfile:
    user_id: str
    interest_tags: Dict[str, float] = field(default_factory=dict)
    lifecycle_stage: UserLifecycle = UserLifecycle.NEW
    recent_intent: Optional[str] = None
    intent_confidence: float = 0.0
    first_active_time: Optional[datetime] = None
    last_active_time: Optional[datetime] = None
    total_orders: int = 0
    total_spent: float = 0.0
    avg_order_value: float = 0.0
    purchase_frequency: float = 0.0
    last_purchase_time: Optional[datetime] = None
    browse_categories: List[str] = field(default_factory=list)
    preferred_price_range: Optional[tuple] = None
    preferred_brands: List[str] = field(default_factory=list)


@dataclass
class RecommendItem:
    item_id: str
    item_type: RecommendType
    title: str
    image_url: Optional[str] = None
    target_url: Optional[str] = None
    price: Optional[float] = None
    original_price: Optional[float] = None
    tags: List[str] = field(default_factory=list)
    category: Optional[str] = None
    brand: Optional[str] = None
    predicted_ctr: float = 0.0
    score: float = 0.0
    reason: Optional[str] = None


@dataclass
class PushRecord:
    push_id: str
    user_id: str
    channel: PushChannel
    recommend_items: List[RecommendItem]
    send_time: Optional[datetime] = None
    status: str = "pending"
    open_time: Optional[datetime] = None
    click_time: Optional[datetime] = None
    conversion_time: Optional[datetime] = None
    conversion_value: float = 0.0
    error_message: Optional[str] = None
    ab_test_group: Optional[str] = None


@dataclass
class Rule:
    rule_id: str
    name: str
    description: str
    priority: int = 0
    conditions: Dict = field(default_factory=dict)
    actions: Dict = field(default_factory=dict)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    enabled: bool = True
    created_by: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
