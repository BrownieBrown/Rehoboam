"""Telegram notification support for Rehoboam trading bot"""

import re
from dataclasses import dataclass
from datetime import datetime

import requests

from .analyzer import PlayerAnalysis, SellCandidate


@dataclass
class SquadSummary:
    """Summary data about user's squad"""

    budget: int
    team_value: int
    best_eleven_points: int
    position_gaps: list[str]  # e.g., ["Need 1 DEF"]


class TelegramNotifier:
    """Sends notifications to Telegram"""

    TELEGRAM_API_BASE = "https://api.telegram.org/bot"
    MAX_MESSAGE_LENGTH = 4096

    def __init__(self, bot_token: str, chat_id: str):
        """
        Initialize Telegram notifier.

        Args:
            bot_token: Telegram bot token from @BotFather
            chat_id: Target chat ID to send messages to
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"{self.TELEGRAM_API_BASE}{bot_token}"

    def send_message(self, text: str, parse_mode: str = "MarkdownV2") -> bool:
        """
        Send a message to Telegram.

        Args:
            text: Message text (supports MarkdownV2 formatting)
            parse_mode: Parse mode ("MarkdownV2", "HTML", or None for plain text)

        Returns:
            True if sent successfully, False otherwise
        """
        # Split message if too long
        messages = self._split_message(text)

        success = True
        for msg in messages:
            try:
                payload = {
                    "chat_id": self.chat_id,
                    "text": msg,
                    "disable_web_page_preview": True,
                }
                if parse_mode:
                    payload["parse_mode"] = parse_mode

                response = requests.post(
                    f"{self.api_url}/sendMessage",
                    json=payload,
                    timeout=30,
                )

                if not response.ok:
                    # Try plain text if MarkdownV2 fails
                    if parse_mode == "MarkdownV2":
                        payload["text"] = self._strip_markdown(msg)
                        del payload["parse_mode"]
                        response = requests.post(
                            f"{self.api_url}/sendMessage",
                            json=payload,
                            timeout=30,
                        )
                        if not response.ok:
                            success = False
                    else:
                        success = False
            except requests.RequestException:
                success = False

        return success

    def send_error_notification(self, error: str) -> bool:
        """
        Send an error notification.

        Args:
            error: Error message

        Returns:
            True if sent successfully
        """
        text = self._escape_markdown(f"REHOBOAM ERROR\n\n{error}")
        return self.send_message(text)

    def format_daily_digest(
        self,
        buy_analyses: list[PlayerAnalysis],
        sell_candidates: list[SellCandidate] | None,
        squad_summary: SquadSummary | None,
        league_name: str,
    ) -> str:
        """
        Format a daily digest message for Telegram.

        Args:
            buy_analyses: List of BUY recommendations
            sell_candidates: List of SELL recommendations (optional)
            squad_summary: Squad summary data (optional)
            league_name: Name of the league

        Returns:
            Formatted message string (MarkdownV2)
        """
        lines = []

        # Header
        date_str = datetime.now().strftime("%d %B %Y")
        lines.append("*REHOBOAM DAILY DIGEST*")
        lines.append(f"_{self._escape_markdown(date_str)} | {self._escape_markdown(league_name)}_")
        lines.append("")

        # Buy opportunities
        if buy_analyses:
            lines.append(self._section_divider())
            lines.append(f"*BUY NOW* \\({len(buy_analyses)} opportunities\\)")
            lines.append(self._section_divider())
            lines.append("")

            for i, analysis in enumerate(buy_analyses[:5], 1):  # Top 5
                lines.extend(self._format_buy_player(i, analysis))
                lines.append("")
        else:
            lines.append(self._section_divider())
            lines.append("*BUY NOW*")
            lines.append(self._section_divider())
            lines.append("_No buy opportunities found_")
            lines.append("")

        # Sell candidates
        if sell_candidates:
            urgent_sells = [s for s in sell_candidates if s.expendability_score >= 60]
            if urgent_sells:
                lines.append(self._section_divider())
                lines.append(f"*SELL NOW* \\({len(urgent_sells)} urgent\\)")
                lines.append(self._section_divider())
                lines.append("")

                for i, candidate in enumerate(urgent_sells[:3], 1):  # Top 3
                    lines.extend(self._format_sell_player(i, candidate))
                    lines.append("")

        # Squad summary
        if squad_summary:
            lines.append(self._section_divider())
            lines.append("*SQUAD*")
            lines.append(self._section_divider())
            lines.append(f"`Budget:      {self._format_price(squad_summary.budget)}`")
            lines.append(f"`Team Value: {self._format_price(squad_summary.team_value)}`")
            lines.append(f"`Best 11:   {squad_summary.best_eleven_points} pts/wk`")
            lines.append("")

            if squad_summary.position_gaps:
                gaps_str = ", ".join(squad_summary.position_gaps)
                lines.append(f"Gaps: {self._escape_markdown(gaps_str)}")
                lines.append("")

        # Market intel summary
        if buy_analyses:
            lines.extend(self._format_market_intel(buy_analyses))

        return "\n".join(lines)

    def _format_buy_player(self, index: int, analysis: PlayerAnalysis) -> list[str]:
        """Format a single buy recommendation."""
        lines = []

        player = analysis.player
        name = f"{player.first_name} {player.last_name}"
        position = self._get_position_abbrev(player.position)

        # Player name and position
        lines.append(f"*{index}\\. {self._escape_markdown(name)}* `{position}`")

        # Smart bid price (use current_price as the bid amount)
        bid_price = analysis.current_price
        market_value = analysis.market_value
        if market_value > 0:
            bid_pct = ((bid_price - market_value) / market_value) * 100
            bid_sign = "+" if bid_pct >= 0 else ""
            lines.append(
                f"`Smart Bid: {self._format_price(bid_price)}` \\({bid_sign}{bid_pct:.0f}%\\)"
            )
        else:
            lines.append(f"`Smart Bid: {self._format_price(bid_price)}`")

        # Score and confidence
        confidence_pct = int(analysis.confidence * 100)
        lines.append(f"`Score: {analysis.value_score:.0f}` | `Conf: {confidence_pct}%`")

        # Trend and PPM
        trend_str = self._format_trend(analysis.trend, analysis.trend_change_pct)
        ppm_str = f"PPM: {analysis.avg_points_per_million:.1f}"
        lines.append(f"{trend_str} | {self._escape_markdown(ppm_str)}")

        # Risk and schedule
        risk_str = self._format_risk(analysis.risk_metrics)
        schedule_str = self._format_schedule(analysis.metadata)
        lines.append(f"{risk_str} | {schedule_str}")

        # Roster impact
        if analysis.roster_impact:
            impact = analysis.roster_impact
            if impact.impact_type == "fills_gap":
                lines.append("\\-\\> _Fills position gap_")
            elif impact.impact_type == "upgrade" and impact.replaces_player:
                gain = impact.value_score_gain
                lines.append(
                    f"\\-\\> _Upgrades {self._escape_markdown(impact.replaces_player)} \\(\\+{gain:.0f} pts\\)_"
                )

        return lines

    def _format_sell_player(self, index: int, candidate: SellCandidate) -> list[str]:
        """Format a single sell recommendation."""
        lines = []

        player = candidate.player
        name = f"{player.first_name} {player.last_name}"
        position = self._get_position_abbrev(player.position)

        # Player name with urgency indicator
        urgency = " [!]" if candidate.expendability_score >= 70 else ""
        lines.append(f"*{index}\\. {self._escape_markdown(name)}* `{position}`{urgency}")

        # Current value
        lines.append(f"`Value:   {self._format_price(candidate.market_value)}`")

        # Profit/Loss
        pl_pct = candidate.profit_loss_pct
        pl_sign = "+" if pl_pct >= 0 else ""
        # Calculate absolute P/L if we had purchase price
        lines.append(f"`P/L: {pl_sign}{pl_pct:.0f}%`")

        # Trend and recommendation
        trend_str = self._format_trend(candidate.trend, None)
        if candidate.recovery_signal:
            lines.append(f"{trend_str} | Signal: {candidate.recovery_signal}")
        else:
            lines.append(trend_str)

        # Reason
        if candidate.protection_reason:
            lines.append(f"\\-\\> _{self._escape_markdown(candidate.protection_reason)}_")
        elif candidate.expendability_score >= 70:
            lines.append("\\-\\> _Take profit now_")
        elif candidate.sos_rating in ["Difficult", "Very Difficult"]:
            lines.append("\\-\\> _Sell before hard fixtures_")

        return lines

    def _format_market_intel(self, analyses: list[PlayerAnalysis]) -> list[str]:
        """Format market intelligence summary."""
        lines = []

        lines.append(self._section_divider())
        lines.append("*MARKET INTEL*")
        lines.append(self._section_divider())

        # Count easy schedules
        easy_schedules = sum(
            1
            for a in analyses
            if a.metadata and a.metadata.get("sos_rating") in ["Easy", "Very Easy"]
        )

        # Count rising trends
        rising_trends = sum(1 for a in analyses if a.trend and "rising" in a.trend.lower())

        # Calculate average discount
        discounts = []
        for a in analyses:
            if a.market_value > 0 and a.current_price > 0:
                discount = ((a.market_value - a.current_price) / a.market_value) * 100
                if discount > 0:
                    discounts.append(discount)

        avg_discount = sum(discounts) / len(discounts) if discounts else 0

        lines.append(f"\\- {easy_schedules} easy schedules available")
        lines.append(f"\\- {rising_trends} strong upward trends")
        if avg_discount > 0:
            lines.append(f"\\- Avg discount: {avg_discount:.0f}%")

        return lines

    def _section_divider(self) -> str:
        """Return a section divider line."""
        return "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-"

    def _format_price(self, price: int) -> str:
        """Format price with thousand separators, right-aligned."""
        return f"{price:>12,}".replace(",", ".")

    def _format_trend(self, trend: str | None, change_pct: float | None) -> str:
        """Format trend indicator."""
        if not trend:
            return "Trend: \\-"

        if "rising" in trend.lower():
            arrow = "\\>"
            if change_pct and change_pct > 0:
                return f"Trend: \\+{change_pct:.0f}% {arrow}"
        elif "falling" in trend.lower():
            arrow = "\\<"
            if change_pct and change_pct < 0:
                return f"Trend: {change_pct:.0f}% {arrow}"
        else:
            arrow = "\\-\\>"

        return f"Trend: {self._escape_markdown(trend)} {arrow}"

    def _format_risk(self, risk_metrics) -> str:
        """Format risk indicator."""
        if not risk_metrics:
            return "Risk: \\-"

        category = risk_metrics.risk_category
        if category == "Low Risk":
            return "Risk: Low"
        elif category == "Medium Risk":
            return "Risk: Med"
        elif category == "High Risk":
            return "Risk: High"
        else:
            return "Risk: VHigh"

    def _format_schedule(self, metadata: dict | None) -> str:
        """Format schedule strength indicator."""
        if not metadata or "sos_rating" not in metadata:
            return "Next 3: \\-"

        rating = metadata["sos_rating"]
        if rating == "Very Easy":
            return "Next 3: [!][!][!]"
        elif rating == "Easy":
            return "Next 3: [!][!]"
        elif rating == "Difficult":
            return "Next 3: [x][x]"
        elif rating == "Very Difficult":
            return "Next 3: [x][x][x]"
        else:
            return "Next 3: \\-"

    def _get_position_abbrev(self, position: str) -> str:
        """Get position abbreviation."""
        abbrevs = {
            "Goalkeeper": "GK",
            "Defender": "DEF",
            "Midfielder": "MID",
            "Forward": "FW",
        }
        return abbrevs.get(position, position[:3].upper())

    def _escape_markdown(self, text: str) -> str:
        """Escape special characters for MarkdownV2."""
        # Characters that need escaping in MarkdownV2
        special_chars = r"_*[]()~`>#+-=|{}.!"
        escaped = ""
        for char in text:
            if char in special_chars:
                escaped += "\\" + char
            else:
                escaped += char
        return escaped

    def _strip_markdown(self, text: str) -> str:
        """Strip Markdown formatting for plain text fallback."""
        # Remove escape characters
        text = re.sub(r"\\(.)", r"\1", text)
        # Remove formatting markers
        text = re.sub(r"[*_`]", "", text)
        return text

    def _split_message(self, text: str) -> list[str]:
        """Split message if it exceeds Telegram's limit."""
        if len(text) <= self.MAX_MESSAGE_LENGTH:
            return [text]

        messages = []
        current = ""

        for line in text.split("\n"):
            if len(current) + len(line) + 1 > self.MAX_MESSAGE_LENGTH - 100:  # Buffer
                if current:
                    messages.append(current.strip())
                current = line + "\n"
            else:
                current += line + "\n"

        if current.strip():
            messages.append(current.strip())

        return messages
