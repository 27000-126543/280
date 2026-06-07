import sys
sys.path.insert(0, '.')

from app import *

print("\n" + "=" * 60)
print("🧪 开始系统测试...")
print("=" * 60 + "\n")

try:
    init_item_pool()
    print(f"✅ 商品池: {len(db.items_pool)} 条")

    init_users()
    print(f"✅ 用户数: {len(db.users)} 个")

    simulate_behavior_events(500)
    print(f"✅ 行为记录: {sum(len(v) for v in db.user_behaviors.values())} 条")

    generate_all_user_recommendations()
    print(f"✅ 推荐结果: {len(db.recommendations)} 个")

    create_push_records()
    print(f"✅ 推送记录: {len(db.push_records)} 条")

    _create_rule_core(
        name="测试规则",
        description="测试",
        priority=10,
        conditions={"lifecycle_stage": "active"},
        actions={"boost_tags": ["热门"], "boost_weight": 2.0},
    )
    print(f"✅ 推荐规则: {len(db.rules)} 条")

    stats = get_summary_stats()
    print(f"\n📊 系统统计:")
    print(f"   - 总用户: {stats['total_users']}")
    print(f"   - 总曝光: {stats['total_impressions']}")
    print(f"   - 总点击: {stats['total_clicks']}")
    print(f"   - CTR: {stats['ctr']*100:.2f}%")

    top_items = get_top_items(5)
    print(f"\n🔥 热门商品 TOP 5:")
    for item in top_items:
        print(f"   - {item['title']} ({item['clicks']}次点击)")

    print("\n" + "=" * 60)
    print("🎉 所有测试通过！系统运行正常！")
    print("=" * 60)

except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
