import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scheduler.main import Scheduler


def print_banner():
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║     企业级用户行为分析与个性化推荐自动化系统                   ║
║     Enterprise User Behavior Analysis & Recommendation       ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def print_help():
    help_text = """
使用方法:
    python run.py [命令]

命令列表:
    demo        - 运行系统演示，展示完整功能流程
    start       - 启动定时调度服务，自动执行每日任务
    collect     - 启动数据采集服务，实时接收用户行为数据
    process     - 启动实时处理服务，计算用户标签和意图
    recommend   - 为所有用户生成个性化推荐
    push        - 执行多渠道推送任务
    report      - 生成今日数据报告
    check       - 检查模型性能并触发调整建议
    query       - 查询推荐明细（需配合参数）

示例:
    python run.py demo          # 运行功能演示
    python run.py start         # 启动完整服务
    python run.py report        # 立即生成报告
"""
    print(help_text)


def main():
    print_banner()

    if len(sys.argv) < 2:
        print_help()
        return

    command = sys.argv[1].lower()
    scheduler = Scheduler()

    try:
        if command == "demo":
            print("▶  运行系统演示...\n")
            scheduler.run_demo()

        elif command == "start":
            print("▶  启动调度服务... (按 Ctrl+C 停止)\n")
            scheduler.start()

        elif command == "collect":
            print("▶  启动数据采集服务...\n")
            scheduler.realtime_processor.process_stream()

        elif command == "process":
            print("▶  执行实时处理...\n")
            scheduler.submit_task("process_behavior_events", {})

        elif command == "recommend":
            print("▶  生成个性化推荐...\n")
            scheduler.submit_task("generate_recommendations", {})

        elif command == "push":
            print("▶  执行多渠道推送...\n")
            from common.models import PushChannel

            scheduler.submit_task(
                "push_to_users", {"channel": PushChannel.IN_APP}
            )

        elif command == "report":
            print("▶  生成数据报告...\n")
            scheduler.submit_task("generate_report", {})

        elif command == "check":
            print("▶  检查模型性能...\n")
            result = scheduler.model_monitor.check_model_performance()
            print(f"检查结果: {result}")

        elif command == "query":
            print("▶  查询统计摘要...\n")
            stats = scheduler.query_service.query_statistics_summary()
            for key, value in stats.items():
                print(f"  {key}: {value}")

        else:
            print(f"❌  未知命令: {command}")
            print_help()

    except KeyboardInterrupt:
        print("\n⏹  用户中断，正在停止服务...")
        scheduler.stop()
    except Exception as e:
        print(f"❌  执行错误: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
