import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from config.settings import REPORT_CONFIG, REDIS_CONFIG
from common.models import PushChannel
from common.logger import LoggerManager
from analytics.effect_tracker import EffectTracker
import redis


class ReportGenerator:
    def __init__(self):
        self.logger = LoggerManager.get_logger("report")
        self.redis_client = redis.Redis(**REDIS_CONFIG)
        self.effect_tracker = EffectTracker()
        self.output_dir = REPORT_CONFIG["output_dir"]
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_daily_report(self, report_date: Optional[datetime] = None) -> Dict:
        if report_date is None:
            report_date = datetime.now() - timedelta(days=1)

        report_date_str = report_date.strftime("%Y%m%d")
        self.logger.info(f"Generating daily report for {report_date_str}")

        try:
            report_data = self._collect_report_data(report_date)

            excel_path = None
            pdf_path = None

            if "excel" in REPORT_CONFIG["formats"]:
                excel_path = self._generate_excel_report(report_data, report_date_str)

            if "pdf" in REPORT_CONFIG["formats"]:
                pdf_path = self._generate_pdf_report(report_data, report_date_str)

            report_summary = {
                "report_date": report_date_str,
                "generated_at": datetime.now().isoformat(),
                "excel_path": excel_path,
                "pdf_path": pdf_path,
                "summary": report_data["summary"],
            }

            self._cache_report(report_date_str, report_summary)
            LoggerManager.log_operation(
                "report",
                "generate_daily_report",
                details=f"date={report_date_str}, excel={excel_path is not None}, pdf={pdf_path is not None}",
            )
            return report_summary

        except Exception as e:
            LoggerManager.log_error("report", "generate_daily_report", e)
            return {"error": str(e)}

    def _collect_report_data(self, report_date: datetime) -> Dict:
        trend_days = REPORT_CONFIG["trend_days"]
        trend_data = self.effect_tracker.get_trend_data(trend_days)
        channel_comparison = self.effect_tracker.get_channel_comparison(report_date)
        overall_stats = self.effect_tracker.get_daily_stats(report_date)

        from analytics.model_monitor import ModelMonitor
        monitor = ModelMonitor()
        performance_check = monitor.check_model_performance()

        from analytics.effect_tracker import ABTester
        ab_tester = ABTester()
        ab_results = self.effect_tracker.get_ab_test_results(days=7)

        user_stats = self._get_user_stats()
        top_items = self._get_top_performing_items(report_date)

        return {
            "report_date": report_date.strftime("%Y-%m-%d"),
            "summary": overall_stats,
            "trend_data": trend_data,
            "channel_comparison": channel_comparison,
            "model_performance": performance_check,
            "ab_test_results": ab_results,
            "user_stats": user_stats,
            "top_items": top_items,
        }

    def _get_user_stats(self) -> Dict:
        try:
            active_users = self.redis_client.scard("active_users_today") or 0
            total_users = self.redis_client.scard("all_users") or 0
            new_users = self.redis_client.scard("new_users_today") or 0

            return {
                "total_users": int(total_users),
                "active_users": int(active_users),
                "new_users": int(new_users),
                "active_rate": int(active_users) / int(total_users) if total_users > 0 else 0,
            }
        except Exception as e:
            LoggerManager.log_error("report", "_get_user_stats", e)
            return {}

    def _get_top_performing_items(self, date: datetime, top_n: int = 10) -> List[Dict]:
        top_items = []
        try:
            date_key = date.strftime("%Y%m%d")
            item_clicks_key = f"stats:item_clicks:{date_key}"
            item_data = self.redis_client.hgetall(item_clicks_key)

            sorted_items = sorted(
                item_data.items(), key=lambda x: int(x[1] if isinstance(x[1], (int, float)) else x[1].decode()), reverse=True
            )

            for item_id, clicks in sorted_items[:top_n]:
                item_id_str = item_id.decode() if isinstance(item_id, bytes) else item_id
                clicks_int = int(clicks.decode()) if isinstance(clicks, bytes) else int(clicks)
                top_items.append({"item_id": item_id_str, "clicks": clicks_int})

        except Exception as e:
            LoggerManager.log_error("report", "_get_top_performing_items", e)
        return top_items

    def _generate_excel_report(self, report_data: Dict, report_date_str: str) -> str:
        try:
            filename = f"daily_report_{report_date_str}.xlsx"
            filepath = os.path.join(self.output_dir, filename)

            with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
                summary_df = pd.DataFrame([report_data["summary"]])
                summary_df.to_excel(writer, sheet_name="汇总", index=False)

                trend_df = pd.DataFrame(report_data["trend_data"])
                trend_df.to_excel(writer, sheet_name="趋势数据", index=False)

                channel_data = []
                for channel, stats in report_data["channel_comparison"].items():
                    channel_data.append(stats)
                if channel_data:
                    channel_df = pd.DataFrame(channel_data)
                    channel_df.to_excel(writer, sheet_name="渠道对比", index=False)

                if report_data["top_items"]:
                    top_items_df = pd.DataFrame(report_data["top_items"])
                    top_items_df.to_excel(writer, sheet_name="热门商品", index=False)

                if report_data.get("ab_test_results"):
                    ab_data = []
                    for group, stats in report_data["ab_test_results"].items():
                        if group != "lift":
                            stats["group"] = group
                            ab_data.append(stats)
                    if ab_data:
                        ab_df = pd.DataFrame(ab_data)
                        ab_df.to_excel(writer, sheet_name="AB测试", index=False)

                self.logger.info(f"Excel report generated: {filepath}")
                return filepath
        except Exception as e:
            LoggerManager.log_error("report", "_generate_excel_report", e)
            return ""

    def _generate_pdf_report(self, report_data: Dict, report_date_str: str) -> str:
        try:
            from matplotlib.backends.backend_pdf import PdfPages

            filename = f"daily_report_{report_date_str}.pdf"
            filepath = os.path.join(self.output_dir, filename)

            with PdfPages(filepath) as pdf:
                fig = plt.figure(figsize=(12, 16))
                gs = fig.add_gridspec(4, 2, hspace=0.4, wspace=0.3)

                self._plot_summary_card(fig.add_subplot(gs[0, :]), report_data["summary"])
                self._plot_ctr_trend(fig.add_subplot(gs[1, 0]), report_data["trend_data"])
                self._plot_conversion_trend(fig.add_subplot(gs[1, 1]), report_data["trend_data"])
                self._plot_revenue_trend(fig.add_subplot(gs[2, 0]), report_data["trend_data"])
                self._plot_channel_comparison(fig.add_subplot(gs[2, 1]), report_data["channel_comparison"])
                self._plot_top_items(fig.add_subplot(gs[3, 0]), report_data["top_items"])
                self._plot_ab_results(fig.add_subplot(gs[3, 1]), report_data.get("ab_test_results", {}))

                fig.suptitle(
                    f"用户行为分析与个性化推荐日报 - {report_data['report_date']}",
                    fontsize=16,
                    fontweight="bold",
                    y=0.99,
                )
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)

            self.logger.info(f"PDF report generated: {filepath}")
            return filepath
        except Exception as e:
            LoggerManager.log_error("report", "_generate_pdf_report", e)
            return ""

    def _plot_summary_card(self, ax, summary: Dict):
        ax.axis("off")
        if not summary:
            return

        metrics = [
            ("曝光量", summary.get("impressions", 0), "{:,.0f}"),
            ("点击量", summary.get("clicks", 0), "{:,.0f}"),
            ("转化率", summary.get("conversions", 0), "{:,.0f}"),
            ("点击率", summary.get("ctr", 0) * 100, "{:.2f}%"),
            ("转化率", summary.get("cvr", 0) * 100, "{:.2f}%"),
            ("收入", summary.get("revenue", 0), "¥{:,.2f}"),
        ]

        for i, (name, value, fmt) in enumerate(metrics):
            row = i // 3
            col = i % 3
            x = 0.15 + col * 0.3
            y = 0.7 - row * 0.4

            rect = plt.Rectangle((x - 0.1, y - 0.15), 0.25, 0.25, facecolor="#f0f8ff", edgecolor="#4a90d9", linewidth=2)
            ax.add_patch(rect)
            ax.text(x, y + 0.05, name, ha="center", va="center", fontsize=10, color="#666")
            ax.text(x, y - 0.05, fmt.format(value), ha="center", va="center", fontsize=14, fontweight="bold", color="#333")

        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)

    def _plot_ctr_trend(self, ax, trend_data: List[Dict]):
        if not trend_data:
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center")
            return

        dates = [datetime.strptime(d["date"], "%Y%m%d") for d in trend_data]
        ctr_values = [d.get("ctr", 0) * 100 for d in trend_data]

        ax.plot(dates, ctr_values, marker="o", linewidth=2, color="#3498db")
        ax.fill_between(dates, ctr_values, alpha=0.3, color="#3498db")

        ax.set_title("点击率(CTR)趋势", fontsize=12, fontweight="bold")
        ax.set_ylabel("CTR (%)")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

    def _plot_conversion_trend(self, ax, trend_data: List[Dict]):
        if not trend_data:
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center")
            return

        dates = [datetime.strptime(d["date"], "%Y%m%d") for d in trend_data]
        cvr_values = [d.get("cvr", 0) * 100 for d in trend_data]

        ax.plot(dates, cvr_values, marker="s", linewidth=2, color="#2ecc71")
        ax.fill_between(dates, cvr_values, alpha=0.3, color="#2ecc71")

        ax.set_title("转化率(CVR)趋势", fontsize=12, fontweight="bold")
        ax.set_ylabel("CVR (%)")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

    def _plot_revenue_trend(self, ax, trend_data: List[Dict]):
        if not trend_data:
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center")
            return

        dates = [datetime.strptime(d["date"], "%Y%m%d") for d in trend_data]
        revenue_values = [d.get("revenue", 0) for d in trend_data]

        ax.bar(dates, revenue_values, color="#e74c3c", alpha=0.7, width=0.8)

        ax.set_title("每日收入趋势", fontsize=12, fontweight="bold")
        ax.set_ylabel("收入 (¥)")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.grid(True, alpha=0.3, axis="y")
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

    def _plot_channel_comparison(self, ax, channel_comparison: Dict):
        if not channel_comparison:
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center")
            return

        channels = list(channel_comparison.keys())
        impressions = [channel_comparison[ch].get("impressions", 0) for ch in channels]
        clicks = [channel_comparison[ch].get("clicks", 0) for ch in channels]

        x = range(len(channels))
        width = 0.35

        bars1 = ax.bar([i - width / 2 for i in x], impressions, width, label="曝光量", color="#3498db", alpha=0.7)
        bars2 = ax.bar([i + width / 2 for i in x], clicks, width, label="点击量", color="#e74c3c", alpha=0.7)

        ax.set_title("各渠道表现对比", fontsize=12, fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels([ch for ch in channels])
        ax.legend()
        ax.grid(True, alpha=0.3, axis="y")

    def _plot_top_items(self, ax, top_items: List[Dict]):
        if not top_items:
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center")
            return

        item_ids = [item["item_id"][:8] + "..." for item in top_items]
        clicks = [item["clicks"] for item in top_items]

        y_pos = range(len(item_ids))
        ax.barh(y_pos, clicks, color="#9b59b6", alpha=0.7)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(item_ids)
        ax.invert_yaxis()
        ax.set_title("热门商品TOP10", fontsize=12, fontweight="bold")
        ax.set_xlabel("点击量")
        ax.grid(True, alpha=0.3, axis="x")

    def _plot_ab_results(self, ax, ab_results: Dict):
        if not ab_results or "treatment" not in ab_results:
            ax.text(0.5, 0.5, "暂无AB测试数据", ha="center", va="center")
            return

        groups = ["对照组", "实验组"]
        ctr_values = [
            ab_results.get("control", {}).get("ctr", 0) * 100,
            ab_results.get("treatment", {}).get("ctr", 0) * 100,
        ]
        cvr_values = [
            ab_results.get("control", {}).get("cvr", 0) * 100,
            ab_results.get("treatment", {}).get("cvr", 0) * 100,
        ]

        x = range(len(groups))
        width = 0.35

        ax.bar([i - width / 2 for i in x], ctr_values, width, label="CTR (%)", color="#3498db", alpha=0.7)
        ax.bar([i + width / 2 for i in x], cvr_values, width, label="CVR (%)", color="#2ecc71", alpha=0.7)

        ax.set_title("AB测试效果对比", fontsize=12, fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels(groups)
        ax.legend()
        ax.grid(True, alpha=0.3, axis="y")

        if "lift" in ab_results:
            lift = ab_results["lift"]
            lift_text = f"CTR提升: {lift.get('ctr', 0)*100:.1f}%\nCVR提升: {lift.get('cvr', 0)*100:.1f}%"
            ax.text(0.5, 0.9, lift_text, transform=ax.transAxes, ha="center", fontsize=9, bbox=dict(facecolor="yellow", alpha=0.3))

    def _cache_report(self, report_date_str: str, report_summary: Dict):
        try:
            key = f"daily_report:{report_date_str}"
            self.redis_client.setex(key, timedelta(days=90), json.dumps(report_summary))
        except Exception as e:
            LoggerManager.log_error("report", "_cache_report", e)

    def get_report(self, report_date_str: str) -> Optional[Dict]:
        try:
            key = f"daily_report:{report_date_str}"
            data = self.redis_client.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            LoggerManager.log_error("report", "get_report", e)
        return None

    def generate_reports_for_range(self, start_date: datetime, end_date: datetime) -> List[Dict]:
        reports = []
        current_date = start_date
        while current_date <= end_date:
            report = self.generate_daily_report(current_date)
            reports.append(report)
            current_date += timedelta(days=1)
        return reports
